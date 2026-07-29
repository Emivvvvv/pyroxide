"""One clean-process benchmark cell executor."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import sys
import threading
import time
from pathlib import Path
from typing import Any, Sequence

try:  # Supports both ``python worker.py`` and package imports in tests.
    from . import backends as backend_module
    from .backends import create_backend
    from .environment import ProcessTreeSampler
    from .models import BackendSpec
    from .report import artifact_checksum
    from .workloads import expected_result, run_workload
except ImportError:  # pragma: no cover - exercised by the script entry point.
    from environment import ProcessTreeSampler
    from models import BackendSpec
    from report import artifact_checksum
    from workloads import expected_result, run_workload

    import backends as backend_module
    from backends import create_backend


def _payload(run_id: str, size: int) -> bytes:
    digest = hashlib.sha256(run_id.encode("utf-8")).digest()
    return (digest * ((size + len(digest) - 1) // len(digest)))[:size]


def _workload_name(kind: str) -> str:
    return {"echo": "payload_echo"}.get(kind, kind)


class _PeakRSSMonitor:
    """Sample process-tree RSS while a blocking backend batch is in flight."""

    def __init__(self, interval_seconds: float = 0.005) -> None:
        self._interval_seconds = interval_seconds
        self._sampler = ProcessTreeSampler()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.peak_rss_bytes = 0

    def __enter__(self) -> _PeakRSSMonitor:
        self._sample()
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop.set()
        self._thread.join()
        self._sample()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self._sample()

    def _sample(self) -> None:
        sample = self._sampler.sample()
        if sample.rss_bytes is not None:
            self.peak_rss_bytes = max(self.peak_rss_bytes, sample.rss_bytes)


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_hashes(backend_kind: str) -> dict[str, str]:
    workload_path = Path(__file__).with_name("workloads.py")
    backend_digest = hashlib.sha256(Path(backend_module.__file__).read_bytes())
    if backend_kind.startswith("pyroxide"):
        specification = importlib.util.find_spec("pyroxide._pyroxide")
        if specification is not None and specification.origin is not None:
            backend_digest.update(Path(specification.origin).read_bytes())
    return {
        "workload": _sha256_path(workload_path),
        "backend": backend_digest.hexdigest(),
    }


def _environment_label() -> str:
    gil_checker = getattr(sys, "_is_gil_enabled", None)
    gil = "unknown" if gil_checker is None else "on" if gil_checker() else "off"
    abi = getattr(sys, "abiflags", "") or "default"
    return (
        f"{platform.python_implementation().lower()}-{platform.python_version()}"
        f"-abi-{abi}-gil-{gil}-{platform.system().lower()}-{platform.machine().lower()}"
    )


def _interpreter_id() -> str:
    identifier = f"{sys.version_info.major}.{sys.version_info.minor}"
    gil_checker = getattr(sys, "_is_gil_enabled", None)
    free_threaded = "t" in getattr(sys, "abiflags", "")
    if gil_checker is not None:
        free_threaded = not bool(gil_checker())
    return identifier + ("t" if free_threaded else "")


def _record_base(cell: dict[str, Any]) -> dict[str, Any]:
    hashes = _artifact_hashes(str(cell["backend_kind"]))
    return {
        "schema_version": 1,
        "run_id": cell["run_id"],
        "experiment_id": cell.get(
            "comparison_id",
            f"{cell['workload_id']}-w{cell['workers']}",
        ),
        "workload": _workload_name(cell["workload_kind"]),
        "environment": (
            f"{cell['environment_id']}|{_environment_label()}"
            if cell.get("environment_id")
            else _environment_label()
        ),
        "semantics": "steady_state_batch_makespan",
        "artifact_hashes": hashes,
        "artifact_checksum": artifact_checksum(hashes),
        "backend": cell["backend_id"],
        "block_index": int(cell["block_index"]),
        "workers": int(cell["workers"]),
    }


def error_record(cell: dict[str, Any], error: object) -> dict[str, Any]:
    """Return a report-compatible failed-cell record."""
    return {**_record_base(cell), "status": "error", "error": str(error)}


def execute_cell(cell: dict[str, Any]) -> dict[str, Any]:
    """Run one declared workload batch and return one self-contained raw record."""
    expected_interpreter = cell.get("expected_interpreter_id")
    if expected_interpreter and _interpreter_id() != expected_interpreter:
        raise RuntimeError(
            f"expected Python {expected_interpreter}, observed {_interpreter_id()}"
        )
    payload_seed = (
        f"{cell.get('comparison_id', cell['workload_id'])}:"
        f"b{cell['block_index']}"
    )
    payload = _payload(payload_seed, int(cell["payload_bytes"]))
    workload_name = _workload_name(cell["workload_kind"])
    computed = run_workload(workload_name, payload)
    if computed != expected_result(workload_name, payload):
        raise RuntimeError("workload result did not match its deterministic oracle")

    backend = create_backend(
        BackendSpec(cell["backend_id"], cell["backend_kind"], int(cell["workers"]))
    )
    try:
        gil_checker = getattr(sys, "_is_gil_enabled", None)
        if (
            cell.get("require_gil_disabled")
            and (gil_checker is None or bool(gil_checker()))
        ):
            raise RuntimeError("free-threaded profile requires the GIL to remain disabled")
        payloads = [payload] * int(cell["tasks_per_sample"])
        expected = tuple(expected_result(workload_name, item) for item in payloads)
        with _PeakRSSMonitor() as resources:
            started = time.perf_counter()
            received = tuple(backend.submit_workload(workload_name, payloads))
            elapsed = time.perf_counter() - started
        if received != expected:
            raise RuntimeError(
                "backend returned a result different from the deterministic oracle"
            )
        return {
            **_record_base(cell),
            "status": "ok",
            "latency_seconds": elapsed,
            "throughput_tasks_per_second": len(payloads) / elapsed,
            "peak_process_tree_rss_bytes": resources.peak_rss_bytes,
        }
    finally:
        backend.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one isolated benchmark cell")
    parser.add_argument("--cell-json", required=True)
    args = parser.parse_args(argv)
    try:
        cell = json.loads(args.cell_json)
        if not isinstance(cell, dict):
            raise ValueError("cell must be a JSON object")
        print(json.dumps(execute_cell(cell), sort_keys=True), flush=True)
        return 0
    except (KeyError, TypeError, ValueError, RuntimeError, OSError) as error:
        if "cell" in locals() and isinstance(cell, dict):
            print(json.dumps(error_record(cell, error), sort_keys=True), flush=True)
        else:
            print(json.dumps({"status": "error", "error": str(error)}), flush=True)
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised by the controller.
    raise SystemExit(main())
