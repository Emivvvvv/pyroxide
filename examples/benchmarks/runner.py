"""Plan and coordinate isolated benchmark worker processes."""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10 only.
    import tomli as tomllib

try:  # Supports both ``python runner.py`` and package imports in tests.
    from .models import ExperimentManifest
    from .profile_manifest import experiment_manifest, load_profile
    from .worker import error_record
except ImportError:  # pragma: no cover - exercised by the script entry point.
    from models import ExperimentManifest
    from profile_manifest import experiment_manifest, load_profile
    from worker import error_record


@dataclass(frozen=True, slots=True)
class PlannedCell:
    """One isolated backend invocation in the resolved experiment matrix."""

    run_id: str
    block_index: int
    trial_index: int
    experiment_id: str
    backend_id: str
    workload_id: str
    workload_kind: str
    tasks_per_sample: int
    payload_bytes: int
    backend_kind: str
    workers: int
    comparison_id: str
    environment_id: str
    python_executable: str
    expected_interpreter_id: str
    require_gil_disabled: bool


def plan_manifest(
    manifest: ExperimentManifest,
    *,
    run_prefix: str = "",
    comparison_prefix: str = "",
    environment_id: str | None = None,
    python_executable: str | None = None,
    expected_interpreter_id: str | None = None,
    require_gil_disabled: bool = False,
) -> tuple[PlannedCell, ...]:
    """Resolve timed experiments into balanced, seeded fresh-process blocks."""
    workloads = {workload.id: workload for workload in manifest.workloads}
    backends = {backend.id: backend for backend in manifest.backends}
    timed = [
        experiment
        for experiment in manifest.experiments
        if experiment.repetitions is not None
    ]
    if not timed:
        return ()

    seed_material = ":".join(
        str(experiment.random_seed) for experiment in sorted(timed, key=lambda item: item.id)
    )
    ordered = sorted(timed, key=lambda item: item.id)
    random.Random(seed_material).shuffle(ordered)
    cells: list[PlannedCell] = []
    block_count = max(experiment.repetitions or 0 for experiment in ordered)
    for block_index in range(block_count):
        active = [
            experiment
            for experiment in ordered
            if (experiment.repetitions or 0) > block_index
        ]
        if active:
            rotation = block_index % len(active)
            active = active[rotation:] + active[:rotation]
        for position, experiment in enumerate(active):
            workload = workloads[experiment.workload]
            backend = backends[experiment.backend]
            cells.append(
                PlannedCell(
                    run_id=(
                        f"{run_prefix}{experiment.id}-b{block_index}-p{position}"
                    ),
                    block_index=block_index,
                    trial_index=block_index,
                    experiment_id=experiment.id,
                    backend_id=backend.id,
                    workload_id=workload.id,
                    workload_kind=workload.kind,
                    tasks_per_sample=workload.tasks_per_sample,
                    payload_bytes=workload.payload_bytes,
                    backend_kind=backend.kind,
                    workers=backend.workers,
                    comparison_id=(
                        f"{comparison_prefix}{workload.id}-w{backend.workers}"
                    ),
                    environment_id=environment_id or current_interpreter_id(),
                    python_executable=python_executable or sys.executable,
                    expected_interpreter_id=(
                        expected_interpreter_id or current_interpreter_id()
                    ),
                    require_gil_disabled=require_gil_disabled,
                )
            )
    return tuple(cells)


def current_interpreter_id() -> str:
    """Return the matrix label for the running CPython interpreter."""
    identifier = f"{sys.version_info.major}.{sys.version_info.minor}"
    gil_checker = getattr(sys, "_is_gil_enabled", None)
    free_threaded = "t" in getattr(sys, "abiflags", "")
    if gil_checker is not None:
        free_threaded = not bool(gil_checker())
    return identifier + ("t" if free_threaded else "")


def plan_profile(
    path: str | Path,
    *,
    interpreters: dict[str, str] | None = None,
    physical_workers: int | None = None,
) -> tuple[PlannedCell, ...]:
    """Expand a strict profile into concrete interpreter and worker cells."""
    profile = load_profile(path)
    if profile["profile"]["track"] == "reliability":
        raise ValueError(
            "reliability profiles require a dedicated reliability runner; "
            "the throughput runner cannot execute timed soaks"
        )
    base_manifest = experiment_manifest(profile)
    profile_id = profile["profile"]["id"]
    versions = tuple(
        str(version)
        for version in profile["matrix"].get(
            "python_versions", [current_interpreter_id()]
        )
    )
    requested_levels = profile["matrix"].get(
        "worker_levels", [base_manifest.backends[0].workers]
    )
    if "physical_cores" in requested_levels and physical_workers is None:
        raise ValueError(
            "physical_cores requires --physical-workers with the measured physical core count"
        )
    worker_levels = tuple(
        physical_workers if level == "physical_cores" else int(level)
        for level in requested_levels
    )
    worker_levels = tuple(dict.fromkeys(worker_levels))
    interpreter_map = interpreters or {current_interpreter_id(): sys.executable}

    cells: list[PlannedCell] = []
    for version in versions:
        executable = interpreter_map.get(version)
        if executable is None:
            raise ValueError(f"missing interpreter mapping for Python {version}")
        for workers in worker_levels:
            if workers is None or workers <= 0:
                raise ValueError("resolved worker levels must be positive")
            manifest = replace(
                base_manifest,
                backends=tuple(
                    replace(backend, workers=workers)
                    for backend in base_manifest.backends
                ),
            )
            prefix = f"{profile_id}-{version}-w{workers}-"
            cells.extend(
                plan_manifest(
                    manifest,
                    run_prefix=prefix,
                    comparison_prefix=f"{profile_id}:{version}:",
                    environment_id=f"{profile_id}:{version}",
                    python_executable=executable,
                    expected_interpreter_id=version,
                    require_gil_disabled=(
                        profile["requirements"].get("post_import_gil")
                        == "abort_if_enabled"
                    ),
                )
            )
    return tuple(cells)


def append_jsonl_event(path: Path, event: dict[str, Any]) -> None:
    """Durably append exactly one JSON object to a raw observation stream."""
    encoded = (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    stream = None
    try:
        stream = os.fdopen(descriptor, "ab", closefd=False)
        stream.write(encoded)
        stream.flush()
        os.fsync(descriptor)
    finally:
        if stream is not None:
            stream.close()
        os.close(descriptor)


def _create_raw_output(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND,
        0o600,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _worker_command(cell: PlannedCell) -> list[str]:
    return [
        cell.python_executable,
        "-m",
        "examples.benchmarks.worker",
        "--cell-json",
        json.dumps(asdict(cell), sort_keys=True),
    ]


def _append_worker_records(path: Path, stdout: str, cell: PlannedCell) -> int:
    appended = 0
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("worker record is not an object")
        except (ValueError, json.JSONDecodeError):
            continue
        if record.get("run_id") != cell.run_id:
            continue
        append_jsonl_event(path, record)
        appended += 1
    return appended


SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]


def run_execution(
    manifest: ExperimentManifest,
    output: Path,
    *,
    subprocess_runner: SubprocessRunner = subprocess.run,
) -> int:
    """Execute the resolved cells in clean processes and retain every event."""
    return run_cells(
        plan_manifest(manifest),
        output,
        subprocess_runner=subprocess_runner,
    )


def run_cells(
    cells: Sequence[PlannedCell],
    output: Path,
    *,
    subprocess_runner: SubprocessRunner = subprocess.run,
) -> int:
    """Execute already-resolved cells in clean processes."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        _create_raw_output(output)
    except FileExistsError as error:
        raise FileExistsError(f"refusing to overwrite existing raw data: {output}") from error

    failures = 0
    for cell in cells:
        try:
            completed = subprocess_runner(
                _worker_command(cell), check=False, capture_output=True, text=True
            )
        except OSError as error:
            failures += 1
            append_jsonl_event(output, error_record(asdict(cell), error))
            continue
        appended = _append_worker_records(output, completed.stdout, cell)
        if completed.returncode:
            failures += 1
        if appended == 0:
            append_jsonl_event(
                output,
                error_record(
                    asdict(cell),
                    completed.stderr
                    or f"worker exited with status {completed.returncode}",
                ),
            )
    return failures


def _print_cells(cells: Sequence[PlannedCell]) -> None:
    print("resolved matrix:")
    for cell in cells:
        print(
            f"  block={cell.block_index} backend={cell.backend_id} "
            f"experiment={cell.experiment_id} run_id={cell.run_id}"
        )
    print(f"timed_trials: {len(cells)}")


def _print_plan(manifest: ExperimentManifest) -> None:
    _print_cells(plan_manifest(manifest))


def _interpreter_mapping(values: Sequence[str]) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for value in values:
        try:
            identifier, executable = value.split("=", 1)
        except ValueError as error:
            raise ValueError("--interpreter must use ID=PATH") from error
        if not identifier or not executable:
            raise ValueError("--interpreter must use non-empty ID=PATH")
        mappings[identifier] = executable
    mappings.setdefault(current_interpreter_id(), sys.executable)
    return mappings


def _is_profile(path: Path) -> bool:
    with path.open("rb") as manifest_file:
        return "profile" in tomllib.load(manifest_file)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan isolated benchmark experiments")
    parser.add_argument("--manifest", required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--validate", action="store_true")
    action.add_argument("--plan", action="store_true")
    action.add_argument("--output", type=Path, metavar="PATH")
    parser.add_argument(
        "--interpreter",
        action="append",
        default=[],
        metavar="ID=PATH",
        help="map a profile Python label such as 3.14t to an executable",
    )
    parser.add_argument("--physical-workers", type=int)
    args = parser.parse_args(argv)

    try:
        if _is_profile(args.manifest):
            cells = plan_profile(
                args.manifest,
                interpreters=_interpreter_mapping(args.interpreter),
                physical_workers=args.physical_workers,
            )
            if args.validate:
                print(f"valid profile: {args.manifest}")
                _print_cells(cells)
                return 0
            if args.plan:
                _print_cells(cells)
                return 0
            return 1 if run_cells(cells, args.output) else 0

        manifest = ExperimentManifest.from_toml(args.manifest)
        if args.validate:
            print(f"valid manifest: {args.manifest}")
            _print_plan(manifest)
            return 0
        if args.plan:
            _print_plan(manifest)
            return 0
        return 1 if run_execution(manifest, args.output) else 0
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 2  # pragma: no cover - argparse exits after parser.error().


if __name__ == "__main__":  # pragma: no cover - exercised by the script entry point.
    raise SystemExit(main())
