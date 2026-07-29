"""Validate, plan, and explicitly execute pinned Odoo benchmark environments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 only.
    import tomli as tomllib

if __package__:
    from .fixture import build_fixture_plan
else:  # pragma: no cover - exercised by the documented direct-script interface.
    from fixture import build_fixture_plan

ROOT = Path(__file__).parent
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_PROFILE_SERVICES = {
    "odoo19-py313": "odoo19-py313",
    "odoo19-py314": "odoo19-py314",
    "odoo-master-py314": "odoo20-preview-py314",
}
_PERFORMANCE_MODES = {
    "performance": "0",
    "performance_recycling": "100",
}


class ProfileValidationError(ValueError):
    """A profile cannot safely be prepared or executed."""


def load_profile(path: str | Path) -> dict[str, Any]:
    """Load one static profile manifest without talking to Docker or Odoo."""
    with Path(path).open("rb") as profile_file:
        return tomllib.load(profile_file)


def load_versions(path: str | Path) -> dict[str, Any]:
    """Load the immutable source and image evidence catalogue."""
    with Path(path).open("rb") as versions_file:
        return tomllib.load(versions_file)


def validate_capacity(*, odoo_workers: int, pyroxide_pool_size: int, cpu_budget: int) -> None:
    """Reject an explicit Odoo/Pyroxide process plan above the CPU budget."""
    if min(odoo_workers, pyroxide_pool_size, cpu_budget) <= 0:
        raise ProfileValidationError("worker counts and CPU budget must be positive")
    required = odoo_workers * pyroxide_pool_size
    if required > cpu_budget:
        raise ProfileValidationError(
            f"CPU budget exceeded: {odoo_workers} Odoo workers * "
            f"{pyroxide_pool_size} Pyroxide processes = {required}, budget {cpu_budget}"
        )


def validate_profile(profile: dict[str, Any], versions: dict[str, Any]) -> list[str]:
    """Return preparation blockers; never resolve mutable inputs implicitly."""
    capacity = profile["capacity"]
    validate_capacity(
        odoo_workers=capacity["odoo_workers"],
        pyroxide_pool_size=capacity["pyroxide_pool_size"],
        cpu_budget=capacity["cpu_budget"],
    )
    issues = []
    source = versions["odoo_sources"][profile["profile"]["odoo_source"]]
    if not _GIT_SHA.fullmatch(source["git_sha"]):
        issues.append("git_sha is not an exact Git commit SHA")
    if not _SHA256.fullmatch(source["requirements_sha256"]):
        issues.append("requirements_sha256 is not an exact SHA-256 value")
    for image_name in ("python_image", "postgres_image"):
        image = versions["images"][profile["profile"][image_name]]
        if "@sha256:" not in image["reference"]:
            issues.append(f"{image_name} must use an image digest")
    if "@sha256:" not in versions["images"]["rust186"]["reference"]:
        issues.append("Rust build image must use an image digest")
    addon = versions["addon"]
    expected_addon_hash = addon["addon_tree_sha256"]
    addon_path = ROOT / "addons" / "pyroxide_benchmark"
    if not addon_path.is_dir():
        issues.append(
            "addon tree is unavailable; integrate it at "
            "examples/odoo_benchmark/addons/pyroxide_benchmark before execution"
        )
    elif not _SHA256.fullmatch(expected_addon_hash):
        issues.append("addon_tree_sha256 must be an exact SHA-256 value")
    elif addon_tree_sha256(addon_path) != expected_addon_hash:
        issues.append("addon_tree_sha256 differs from the integrated addon content")
    return issues


def addon_tree_sha256(path: str | Path) -> str:
    """Hash sorted regular-file paths and their content digests for addon evidence.

    The hash is SHA-256 over UTF-8 POSIX-relative paths, a NUL separator, each
    file's hexadecimal SHA-256, and a trailing newline. Symlinks and generated
    ``__pycache__`` files are excluded so the value is portable and reproducible.
    """
    root = Path(path)
    digest = hashlib.sha256()
    for child in sorted(root.rglob("*"), key=lambda candidate: candidate.as_posix()):
        if not child.is_file() or child.is_symlink() or "__pycache__" in child.parts:
            continue
        relative_path = child.relative_to(root).as_posix().encode("utf-8")
        content_bytes = child.read_bytes().replace(b"\r\n", b"\n")
        content_hash = hashlib.sha256(content_bytes).hexdigest().encode("ascii")
        digest.update(relative_path)
        digest.update(b"\0")
        digest.update(content_hash)
        digest.update(b"\n")
    return digest.hexdigest()


def build_plan(profile: dict[str, Any], versions: dict[str, Any]) -> dict[str, Any]:
    """Build a complete resource and measurement plan without side effects."""
    fixture = build_fixture_plan(seed=profile["fixture"]["seed"])
    issues = validate_profile(profile, versions)
    source = versions["odoo_sources"][profile["profile"]["odoo_source"]]
    python_image = versions["images"][profile["profile"]["python_image"]]["reference"]
    postgres_image = versions["images"][profile["profile"]["postgres_image"]]["reference"]
    rust_image = versions["images"]["rust186"]["reference"]
    return {
        "execution": "available_explicit_only",
        "executed_measurement_boundaries": {
            "correctness": [],
            "performance": ["compute_only"],
        },
        "fixture": fixture,
        "health_evidence": profile["health"]["evidence"],
        "measurement_boundaries": profile["measurement"]["boundaries"],
        "planned_measurement_boundaries": profile["measurement"]["boundaries"],
        "odoo_configurations": profile["odoo"],
        "preparation_issues": issues,
        "profile": {
            **profile["profile"],
            "odoo_git_sha": source["git_sha"],
            "postgres_image_reference": postgres_image,
            "python_image_reference": python_image,
            "rust_image_reference": rust_image,
            "requirements_sha256": source["requirements_sha256"],
        },
        "resources": {
            "cpu_budget": profile["capacity"]["cpu_budget"],
            "postgres": "PostgreSQL 16",
            "process_capacity": profile["capacity"]["odoo_workers"]
            * profile["capacity"]["pyroxide_pool_size"],
        },
    }


def build_execution_commands(
    profile: dict[str, Any],
    versions: dict[str, Any],
    *,
    project_name: str,
    mode: str = "correctness",
    output_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve one allow-listed, project-scoped Compose execution."""
    if not _PROJECT_NAME.fullmatch(project_name):
        raise ProfileValidationError(
            "project_name must contain 1-63 lowercase letters, digits, '-' or '_'"
        )
    profile_data = profile["profile"]
    profile_id = profile_data["id"]
    try:
        service = _PROFILE_SERVICES[profile_id]
    except KeyError as error:
        raise ProfileValidationError(f"unknown execution profile: {profile_id}") from error
    source = versions["odoo_sources"][profile_data["odoo_source"]]
    python_image = versions["images"][profile_data["python_image"]]["reference"]
    postgres_image = versions["images"][profile_data["postgres_image"]]["reference"]
    rust_image = versions["images"]["rust186"]["reference"]
    prefix = (
        "docker",
        "compose",
        "--file",
        str(ROOT / "compose.yml"),
        "--project-name",
        project_name,
        "--profile",
        "execution",
    )
    if mode not in {"correctness", *_PERFORMANCE_MODES}:
        raise ProfileValidationError(f"unsupported execution mode: {mode}")
    if mode in _PERFORMANCE_MODES and output_directory is None:
        raise ProfileValidationError("performance mode requires an output directory")
    resolved_output = (
        Path(output_directory).resolve()
        if output_directory is not None
        else (ROOT / "results").resolve()
    )
    return {
        "up": (*prefix, "up", "--detach", "--wait", "postgres"),
        "run": (*prefix, "run", "--rm", "--build", service),
        "down": (*prefix, "down", "--volumes", "--remove-orphans"),
        "environment": {
            "PYROXIDE_ODOO_GIT_SHA": source["git_sha"],
            "PYROXIDE_ODOO_REQUIREMENTS_SHA256": source["requirements_sha256"],
            "PYROXIDE_ODOO_PYTHON_IMAGE": python_image,
            "PYROXIDE_ODOO_POSTGRES_IMAGE": postgres_image,
            "PYROXIDE_ODOO_RUST_IMAGE": rust_image,
            "PYROXIDE_ODOO_MODE": mode,
            "PYROXIDE_ODOO_RELEASE_LABEL": profile_data["release_label"],
            "PYROXIDE_ODOO_RESULTS_DIR": str(resolved_output),
            "PYROXIDE_ODOO_OUTPUT": f"/workspace/results/{_result_name(profile_id, mode)}",
            "PYROXIDE_MAX_TASKS_PER_WORKER": _PERFORMANCE_MODES.get(mode, "100"),
        },
    }


def execute_profile(
    profile: dict[str, Any],
    versions: dict[str, Any],
    *,
    project_name: str,
    mode: str = "correctness",
    output_directory: str | Path | None = None,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    """Execute one validated profile and always remove its scoped resources."""
    issues = validate_profile(profile, versions)
    if issues:
        raise ProfileValidationError("; ".join(issues))
    if mode in _PERFORMANCE_MODES:
        if output_directory is None:
            raise ProfileValidationError("performance mode requires an output directory")
        output_path = Path(output_directory)
        result_path = output_path / _result_name(profile["profile"]["id"], mode)
        if result_path.exists():
            raise FileExistsError(f"refusing to overwrite benchmark result: {result_path}")
        output_path.mkdir(parents=True, exist_ok=True)
    commands = build_execution_commands(
        profile,
        versions,
        project_name=project_name,
        mode=mode,
        output_directory=output_directory,
    )
    environment = {**os.environ, **commands["environment"]}
    try:
        up_result = command_runner(commands["up"], check=False, env=environment, text=True)
        if up_result.returncode:
            return up_result.returncode
        run_result = command_runner(commands["run"], check=False, env=environment, text=True)
        return run_result.returncode
    finally:
        command_runner(commands["down"], check=False, env=environment, text=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate, plan, or explicitly execute one pinned profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=_profile_names())
    parser.add_argument(
        "--project-name",
        help="scoped Compose project name; a unique name is generated when omitted",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("benchmark_results/odoo"),
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--validate", action="store_true")
    action.add_argument("--plan", action="store_true")
    action.add_argument("--execute", action="store_true")
    action.add_argument("--benchmark", action="store_true")
    action.add_argument("--benchmark-recycling", action="store_true")
    args = parser.parse_args(argv)
    profile = load_profile(ROOT / "manifests" / f"{args.profile}.toml")
    versions = load_versions(ROOT / "versions.toml")
    plan = build_plan(profile, versions)
    if args.execute or args.benchmark or args.benchmark_recycling:
        project_name = args.project_name or f"pyroxide-{args.profile}-{uuid.uuid4().hex[:8]}"
        if args.benchmark_recycling:
            mode = "performance_recycling"
        elif args.benchmark:
            mode = "performance"
        else:
            mode = "correctness"
        return execute_profile(
            profile,
            versions,
            project_name=project_name,
            mode=mode,
            output_directory=args.output_directory if mode in _PERFORMANCE_MODES else None,
        )
    if args.validate:
        print(json.dumps({"issues": plan["preparation_issues"], "profile": args.profile}, sort_keys=True))
        return 2 if plan["preparation_issues"] else 0
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def _profile_names() -> tuple[str, ...]:
    return ("odoo19-py313", "odoo19-py314", "odoo-master-py314")


def _result_name(profile_id: str, mode: str) -> str:
    suffix = "-recycling" if mode == "performance_recycling" else ""
    return f"{profile_id}{suffix}.json"


if __name__ == "__main__":  # pragma: no cover - command-line entry point.
    raise SystemExit(main())
