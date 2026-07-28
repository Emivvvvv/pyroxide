"""Check and plan distributed benchmarks without starting services."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

_PACKAGES = {
    "celery": "celery",
    "dask_single_node": "distributed",
    "dramatiq": "dramatiq",
    "ray_single_node": "ray",
    "redis_client": "redis",
}
_EXPECTED_VERSIONS = {
    "celery": "5.6.3",
    "dask_single_node": "2026.7.1",
    "dramatiq": "2.2.0",
    "ray_single_node": "2.56.1",
    "redis_client": "6.4.0",
}


def _installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _docker_status(
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[bool, str | None]:
    completed = command_runner(
        ["docker", "version", "--format", "{{json .Server}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        server = json.loads(completed.stdout)
    except json.JSONDecodeError:
        server = None
    if completed.returncode or not isinstance(server, dict) or not server.get("Version"):
        detail = completed.stderr.strip() or completed.stdout.strip()
        return False, detail or "docker daemon unavailable"
    return True, None


def collect(
    *,
    version_lookup: Callable[[str], str | None] = _installed_version,
    executable_lookup: Callable[[str], str | None] = shutil.which,
    docker_probe: Callable[[], tuple[bool, str | None]] = _docker_status,
) -> dict[str, Any]:
    """Return explicit availability; unavailable competitors are never substituted."""
    components: dict[str, dict[str, Any]] = {}
    for label, package in _PACKAGES.items():
        version = version_lookup(package)
        if version is None:
            components[label] = {
                "available": False,
                "reason": "package not installed",
                "version": None,
            }
        elif version == _EXPECTED_VERSIONS[label]:
            components[label] = {"available": True, "version": version}
        else:
            components[label] = {
                "available": False,
                "reason": f"expected {_EXPECTED_VERSIONS[label]}, found {version}",
                "version": version,
            }

    docker = executable_lookup("docker")
    if docker is None:
        container_runtime = {
            "available": False,
            "executable": None,
            "reason": "docker executable not found",
        }
    else:
        daemon_available, reason = docker_probe()
        container_runtime = {
            "available": daemon_available,
            "executable": docker,
            "reason": reason,
        }

    return {
        "schema_version": 1,
        "comparison_scope": "separate_from_local_executor_tables",
        "components": components,
        "container_runtime": container_runtime,
        "runnable_tracks": {
            "brokers": container_runtime["available"],
            "dask_single_node": components["dask_single_node"]["available"],
            "ray_single_node": components["ray_single_node"]["available"],
        },
    }


def dry_run_plan() -> dict[str, Any]:
    """Return copy/pasteable commands; constructing this plan has no side effects."""
    compose = [
        "docker",
        "compose",
        "-f",
        "examples/benchmarks/compose.brokers.yml",
        "--profile",
        "execution",
    ]
    return {
        "schema_version": 1,
        "comparison_scope": "separate_from_local_executor_tables",
        "commands": {
            "create_host_environment": [
                "uv",
                "venv",
                "--python",
                "3.14",
                ".benchmark-envs/distributed",
            ],
            "install_host_dependencies": [
                "uv",
                "pip",
                "install",
                "--python",
                ".benchmark-envs/distributed/bin/python",
                "-r",
                "examples/benchmarks/requirements-distributed.txt",
            ],
            "broker_services_start": [*compose, "up", "-d", "--wait"],
            "broker_services_stop": [*compose, "down"],
            "celery_smoke": [
                ".benchmark-envs/distributed/bin/python",
                "-m",
                "examples.benchmarks.brokers.benchmark",
                "--backend",
                "celery",
                "--tasks",
                "10",
                "--output",
                "benchmark_results/distributed/celery-smoke.json",
            ],
            "dramatiq_smoke": [
                ".benchmark-envs/distributed/bin/python",
                "-m",
                "examples.benchmarks.brokers.benchmark",
                "--backend",
                "dramatiq",
                "--tasks",
                "10",
                "--output",
                "benchmark_results/distributed/dramatiq-smoke.json",
            ],
            "local_distributed": [
                ".benchmark-envs/distributed/bin/python",
                "-m",
                "examples.benchmarks.runner",
                "--manifest",
                "examples/benchmarks/manifests/distributed.toml",
                "--output",
                "benchmark_results/distributed/raw.jsonl",
            ],
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = dry_run_plan() if args.dry_run else collect()
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
