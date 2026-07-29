"""Run and summarize duration-aware reliability observations."""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from examples.benchmarks.environment import ProcessTreeSampler, collect_environment

if __name__ == "__main__" and __spec__ is not None:
    sys.modules.setdefault(__spec__.name, sys.modules[__name__])

_RESERVED_SUMMARIES: dict[Path, tuple[int, int]] = {}
_RESERVED_OUTPUTS: dict[Path, tuple[int, int]] = {}
_ENGINE_FIELDS = (
    "worker_count",
    "max_processes",
    "queue_capacity",
    "queued_tasks",
    "running_tasks",
    "active_tasks",
    "submitted_tasks",
    "rejected_tasks",
    "completed_tasks",
    "failed_tasks",
    "cancelled_tasks",
)


@dataclass(frozen=True, slots=True)
class ReliabilityConfig:
    """The bounded execution settings for one reliability observation run."""

    duration_seconds: float
    sample_interval_seconds: float
    output: Path
    summary: Path
    seed: int = 1729
    shutdown_grace_seconds: float = 10.0
    workers: int = 2
    queue_capacity: int = 4
    max_processes: int = 2
    max_tasks_per_worker: int = 100


def _identity(value: int) -> int:
    return value


def _isolated_identity(value: int) -> int:
    return value


def _crash_worker(_: int) -> int:
    os._exit(42)


def _sleep_then_identity(payload: tuple[float, int]) -> int:
    delay, value = payload
    time.sleep(delay)
    return value


if __spec__ is not None:
    for _task_definition in (
        _identity,
        _isolated_identity,
        _crash_worker,
        _sleep_then_identity,
    ):
        _task_definition.__module__ = __spec__.name


class _RunState:
    """Serialize controller counters shared by sampling and scenario threads."""

    def __init__(self, *, recycle_target: int) -> None:
        self._lock = threading.Lock()
        self._accepted = 0
        self._completed = 0
        self._failed = 0
        self._cancelled = 0
        self._rejected = 0
        self._incorrect = 0
        self._latencies: list[float] = []
        self._assertion_failures: list[dict[str, str]] = []
        self._post_crash_success = False
        self._post_recycle_success = False
        self._recycle_successes = 0
        self._recycle_target = recycle_target
        self._maximum_child_count: int | None = None
        self._frozen = False

    def accept(self) -> None:
        with self._lock:
            if self._frozen:
                return
            self._accepted += 1

    def reject(self) -> None:
        with self._lock:
            if self._frozen:
                return
            self._rejected += 1

    def finish(self, outcome: str, latency: float, *, incorrect: bool = False) -> None:
        with self._lock:
            if self._frozen:
                return
            if outcome == "completed":
                self._completed += 1
            elif outcome == "failed":
                self._failed += 1
            elif outcome == "cancelled":
                self._cancelled += 1
            else:  # pragma: no cover - internal misuse guard.
                raise ValueError(f"unknown terminal outcome: {outcome}")
            if incorrect:
                self._incorrect += 1
            self._latencies.append(float(max(0.0, latency)))

    def record_assertion_failure(self, name: str, message: str) -> None:
        with self._lock:
            if self._frozen:
                return
            self._assertion_failures.append(
                {
                    "record_type": "assertion_failure",
                    "name": name,
                    "message": message,
                }
            )

    def mark_post_crash_success(self) -> None:
        with self._lock:
            if self._frozen:
                return
            self._post_crash_success = True

    def record_recycle_success(self) -> None:
        with self._lock:
            if self._frozen:
                return
            self._recycle_successes += 1
            if self._recycle_successes >= self._recycle_target:
                self._post_recycle_success = True

    def observe_child_count(self, child_count: int | None) -> None:
        if child_count is None:
            return
        with self._lock:
            if self._frozen:
                return
            if self._maximum_child_count is None:
                self._maximum_child_count = child_count
            else:
                self._maximum_child_count = max(
                    self._maximum_child_count, child_count
                )

    def take_observation(self) -> tuple[dict[str, int | bool], list[float]]:
        with self._lock:
            operations = {
                "accepted_operations": self._accepted,
                "completed_operations": self._completed,
                "failed_operations": self._failed,
                "cancelled_operations": self._cancelled,
                "rejected_operations": self._rejected,
                "incorrect_results": self._incorrect,
                "post_crash_success": self._post_crash_success,
                "post_recycle_success": self._post_recycle_success,
            }
            latencies = self._latencies
            self._latencies = []
            return operations, latencies

    def freeze_and_take_final(
        self,
    ) -> tuple[dict[str, int | bool | None], list[float]]:
        with self._lock:
            self._frozen = True
            final_values = {
                "accepted_operations": self._accepted,
                "completed_operations": self._completed,
                "failed_operations": self._failed,
                "cancelled_operations": self._cancelled,
                "rejected_operations": self._rejected,
                "incorrect_results": self._incorrect,
                "post_crash_success": self._post_crash_success,
                "post_recycle_success": self._post_recycle_success,
                "max_observed_child_count": self._maximum_child_count,
            }
            latencies = self._latencies
            self._latencies = []
            return final_values, latencies

    def assertion_failures(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(failure) for failure in self._assertion_failures]

def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> ReliabilityConfig:
    """Parse bounded reliability settings and prepare their parent directories."""
    parser = argparse.ArgumentParser(
        description="Run duration-aware reliability observations"
    )
    parser.add_argument("--duration-seconds", required=True, type=_positive_float)
    parser.add_argument("--sample-interval-seconds", required=True, type=_positive_float)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--shutdown-grace-seconds", type=_positive_float, default=10.0)
    args = parser.parse_args(argv)
    if args.sample_interval_seconds > args.duration_seconds:
        parser.error("--sample-interval-seconds cannot exceed --duration-seconds")

    config = ReliabilityConfig(
        duration_seconds=args.duration_seconds,
        sample_interval_seconds=args.sample_interval_seconds,
        output=args.output,
        summary=args.summary,
        seed=args.seed,
        shutdown_grace_seconds=args.shutdown_grace_seconds,
    )
    config.output.parent.mkdir(parents=True, exist_ok=True)
    config.summary.parent.mkdir(parents=True, exist_ok=True)
    return config


def _file_identity(status: os.stat_result) -> tuple[int, int, int]:
    return status.st_dev, status.st_ino, getattr(status, "st_ctime_ns", int(getattr(status, "st_ctime", 0) * 1e9))


def _reserve_output(path: Path) -> tuple[int, int, int]:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        status = os.fstat(descriptor)
        return _file_identity(status)
    finally:
        os.close(descriptor)


def _reservation_key(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _unlink_reserved_empty(path: Path, identity: tuple[int, int, int]) -> None:
    try:
        status = os.stat(path, follow_symlinks=False)
        if (
            _file_identity(status) == identity
            and status.st_size == 0
        ):
            path.unlink()
    except FileNotFoundError:
        pass


def reserve_outputs(output: Path, summary: Path) -> None:
    """Create empty raw and summary artifacts without replacing either one."""
    output_key = _reservation_key(output)
    summary_key = summary.resolve()
    try:
        output_identity = _reserve_output(output)
    except FileExistsError as error:
        raise FileExistsError(f"refusing to overwrite existing output: {output}") from error

    try:
        summary_identity = _reserve_output(summary)
    except OSError as error:
        _unlink_reserved_empty(output, output_identity)
        if isinstance(error, FileExistsError):
            raise FileExistsError(
                f"refusing to overwrite existing summary: {summary}"
            ) from error
        raise
    _RESERVED_OUTPUTS[output_key] = output_identity
    _RESERVED_SUMMARIES[summary_key] = summary_identity


def _configure_pyroxide(config: ReliabilityConfig) -> dict[str, str | None]:
    settings = {
        "PYROXIDE_WORKERS": config.workers,
        "PYROXIDE_QUEUE_CAPACITY": config.queue_capacity,
        "PYROXIDE_MAX_PROCESSES": config.max_processes,
        "PYROXIDE_MAX_TASKS_PER_WORKER": config.max_tasks_per_worker,
        "PYROXIDE_QUEUE_TIMEOUT_MS": 0,
    }
    previous = {name: os.environ.get(name) for name in settings}
    for name, value in settings.items():
        os.environ[name] = str(value)
    return previous


def _restore_pyroxide_environment(previous: Mapping[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _engine_snapshot(pyroxide_facade: Any) -> dict[str, int]:
    stats = pyroxide_facade.stats()
    return {field: int(stats[field]) for field in _ENGINE_FIELDS}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resource_snapshot(sampler: Any, state: _RunState) -> dict[str, int | None]:
    sampled = asdict(sampler.sample())
    child_count = sampled["children_total"]
    state.observe_child_count(child_count)
    return {
        "rss_bytes": sampled["rss_bytes"],
        "descriptor_count": sampled["file_descriptors"],
        "child_count": child_count,
    }


class _ProcessTableEntry:
    """Minimal process object for child counts when optional psutil is absent."""

    def __init__(self, pid: int) -> None:
        self.pid = pid

    def cpu_times(self) -> Any:
        raise RuntimeError("CPU process-tree sampling requires psutil")

    def memory_info(self) -> Any:
        raise RuntimeError("RSS process-tree sampling requires psutil")

    def num_fds(self) -> int:
        raise RuntimeError("descriptor process-tree sampling requires psutil")


class _ProcessTableRoot(_ProcessTableEntry):
    def children(self, *, recursive: bool) -> tuple[_ProcessTableEntry, ...]:
        command = ["ps", "-axo", "pid=,ppid="]
        probe = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = probe.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            probe.kill()
            probe.communicate()
            raise
        if probe.returncode:
            raise subprocess.CalledProcessError(
                probe.returncode,
                command,
                output=stdout,
                stderr=stderr,
            )
        children_by_parent: dict[int, list[int]] = {}
        for line in stdout.splitlines():
            pid_text, parent_text = line.split()
            pid = int(pid_text)
            if pid != probe.pid:
                children_by_parent.setdefault(int(parent_text), []).append(pid)

        found: list[int] = []
        pending = list(children_by_parent.get(self.pid, ()))
        while pending:
            child = pending.pop()
            found.append(child)
            if recursive:
                pending.extend(children_by_parent.get(child, ()))
        return tuple(_ProcessTableEntry(pid) for pid in found)


def _default_sampler() -> ProcessTreeSampler:
    try:
        return ProcessTreeSampler()
    except ModuleNotFoundError as error:
        if error.name != "psutil":
            raise
        return ProcessTreeSampler(process=_ProcessTableRoot(os.getpid()))


def _submit(
    submit: Callable[[Any], Any],
    payload: Any,
    state: _RunState,
    monotonic: Callable[[], float],
    *,
    allow_rejection: bool = False,
) -> tuple[Any, float] | None:
    started = monotonic()
    try:
        handle = submit(payload)
    except BufferError:
        if not allow_rejection:
            raise
        state.reject()
        return None
    state.accept()
    return handle, started


def _resolve(
    accepted: tuple[Any, float],
    state: _RunState,
    monotonic: Callable[[], float],
    *,
    expected: int,
    deadline: float | None = None,
    cancellation_requested: bool = False,
    failure_expected: bool = False,
) -> bool:
    handle, started = accepted
    terminal_retry = False
    while True:
        try:
            if deadline is None:
                result = handle.result()
            else:
                timeout_seconds = max(0.0, deadline - monotonic())
                try:
                    result = handle.result(timeout_sec=timeout_seconds)
                except TypeError as error:
                    if "timeout_sec" not in str(error):
                        raise
                    result = handle.result()
            break
        except TimeoutError as error:
            try:
                status = handle.status
            except Exception as status_error:
                state.record_assertion_failure(
                    type(status_error).__name__, str(status_error)
                )
                status = None
            if status in {"Pending", "Running"}:
                if deadline is not None and monotonic() < deadline:
                    continue
                if deadline is not None:
                    deadline = None
                    continue
                state.record_assertion_failure(type(error).__name__, str(error))
                return False
            if status in {"Completed", "Failed", "Cancelled"}:
                if terminal_retry:
                    state.finish("failed", monotonic() - started)
                    state.record_assertion_failure(type(error).__name__, str(error))
                    return False
                terminal_retry = True
                deadline = None
                continue
            state.finish("failed", monotonic() - started)
            state.record_assertion_failure(type(error).__name__, str(error))
            return False
        except Exception as error:
            latency = monotonic() - started
            try:
                status = handle.status
            except Exception as status_error:
                state.record_assertion_failure(
                    type(status_error).__name__, str(status_error)
                )
                status = None
            if cancellation_requested:
                if (
                    status == "Cancelled"
                    and isinstance(error, RuntimeError)
                    and str(error) == "Task cancelled"
                ):
                    state.finish("cancelled", latency)
                    return True
                state.finish("failed", latency)
                state.record_assertion_failure(type(error).__name__, str(error))
                return False
            state.finish("failed", latency)
            if failure_expected:
                if isinstance(error, RuntimeError) and "(crashed/EOF)" in str(error):
                    return True
                state.record_assertion_failure(type(error).__name__, str(error))
                return False
            state.record_assertion_failure(type(error).__name__, str(error))
            return False

    latency = monotonic() - started
    if cancellation_requested:
        state.finish("completed", latency, incorrect=True)
        state.record_assertion_failure(
            "cancelled_operation_completed",
            "a handle reported successful cancellation but returned a result",
        )
        return False
    if failure_expected:
        state.finish("completed", latency, incorrect=True)
        state.record_assertion_failure(
            "expected_crash_missing",
            "the deliberate isolated worker crash returned successfully",
        )
        return False
    correct = result == expected
    state.finish("completed", latency, incorrect=not correct)
    return correct


def _verify_operation(
    submit: Callable[[Any], Any],
    payload: Any,
    state: _RunState,
    monotonic: Callable[[], float],
    *,
    expected: int,
    deadline: float | None = None,
) -> bool:
    accepted = _submit(submit, payload, state, monotonic)
    if accepted is None:  # pragma: no cover - rejection is disabled.
        return False
    return _resolve(
        accepted,
        state,
        monotonic,
        expected=expected,
        deadline=deadline,
    )


def _run_saturation(
    slow_task: Callable[[tuple[float, int]], Any],
    pyroxide_facade: Any,
    config: ReliabilityConfig,
    state: _RunState,
    monotonic: Callable[[], float],
    drain_deadline: float,
    stop: threading.Event,
    cycle: int,
) -> None:
    accepted: list[tuple[Any, float, int]] = []
    observed_rejection = False
    cancelled_handle: Any | None = None
    maximum_submissions = config.workers + config.queue_capacity + 32
    try:
        with pyroxide_facade.scoped(queue_timeout_ms=0):
            for offset in range(maximum_submissions):
                if stop.is_set():
                    state.record_assertion_failure(
                        "scenario_deadline",
                        "duration deadline arrived during queue saturation",
                    )
                    break
                value = cycle * 10_000 + offset
                submitted = _submit(
                    slow_task,
                    (0.05, value),
                    state,
                    monotonic,
                    allow_rejection=True,
                )
                if submitted is None:
                    observed_rejection = True
                    break
                handle, started = submitted
                accepted.append((handle, started, value))

        if not observed_rejection and not stop.is_set():
            state.record_assertion_failure(
                "queue_rejection_missing",
                "bounded submissions did not raise BufferError",
            )

        for handle, _, _ in reversed(accepted):
            if handle.status == "Pending" and handle.cancel():
                cancelled_handle = handle
                break
        if cancelled_handle is None:
            state.record_assertion_failure(
                "pending_cancellation_missing",
                "no accepted pending task could be cancelled",
            )
    finally:
        for handle, started, expected in accepted:
            _resolve(
                (handle, started),
                state,
                monotonic,
                expected=expected,
                deadline=drain_deadline,
                cancellation_requested=handle is cancelled_handle,
            )


def _run_scenario_cycle(
    *,
    cycle: int,
    recycle_submissions: int,
    identity_task: Callable[[int], Any],
    isolated_identity_task: Callable[[int], Any],
    crash_task: Callable[[int], Any],
    slow_task: Callable[[tuple[float, int]], Any],
    pyroxide_facade: Any,
    config: ReliabilityConfig,
    state: _RunState,
    monotonic: Callable[[], float],
    deadline: float,
    drain_deadline: float,
    stop: threading.Event,
) -> None:
    base = cycle * 1_000_000
    _verify_operation(
        identity_task,
        base + 1,
        state,
        monotonic,
        expected=base + 1,
        deadline=drain_deadline,
    )
    if stop.is_set():
        return
    _verify_operation(
        isolated_identity_task,
        base + 2,
        state,
        monotonic,
        expected=base + 2,
        deadline=drain_deadline,
    )
    if stop.is_set():
        return
    _run_saturation(
        slow_task,
        pyroxide_facade,
        config,
        state,
        monotonic,
        drain_deadline,
        stop,
        cycle,
    )
    if stop.is_set():
        return

    crashed = _submit(crash_task, base + 3, state, monotonic)
    crash_observed = False
    if crashed is not None:  # pragma: no branch - rejection is disabled.
        crash_observed = _resolve(
            crashed,
            state,
            monotonic,
            expected=base + 3,
            deadline=drain_deadline,
            failure_expected=True,
        )
    if stop.is_set():
        return
    recovery_succeeded = _verify_operation(
        isolated_identity_task,
        base + 4,
        state,
        monotonic,
        expected=base + 4,
        deadline=drain_deadline,
    )
    if crash_observed and recovery_succeeded:
        state.mark_post_crash_success()

    for offset in range(recycle_submissions):
        if stop.is_set() or monotonic() >= deadline:
            state.record_assertion_failure(
                "recycling_deadline",
                "duration deadline arrived before recycling work completed",
            )
            break
        value = base + 100 + offset
        if _verify_operation(
            isolated_identity_task,
            value,
            state,
            monotonic,
            expected=value,
            deadline=drain_deadline,
        ):
            state.record_recycle_success()


def _recycle_target(config: ReliabilityConfig) -> int:
    boundaries = 2 if config.duration_seconds >= 60 else 1
    return boundaries * config.max_tasks_per_worker + 1


def _planned_cycles(config: ReliabilityConfig) -> int:
    cycle_interval = min(1.0, config.sample_interval_seconds)
    return max(1, math.ceil(config.duration_seconds / cycle_interval))


def _sample_record(
    *,
    elapsed_seconds: float,
    pyroxide_facade: Any,
    sampler: Any,
    state: _RunState,
) -> dict[str, Any]:
    operations, latencies = state.take_observation()
    return {
        "record_type": "sample",
        "timestamp_utc": _utc_timestamp(),
        "elapsed_seconds": float(elapsed_seconds),
        "engine": _engine_snapshot(pyroxide_facade),
        "resources": _resource_snapshot(sampler, state),
        "operations": operations,
        "latency_seconds": latencies,
    }


def _failure(name: str, message: str) -> dict[str, str]:
    return {
        "record_type": "assertion_failure",
        "name": name,
        "message": message,
    }


def _validate_final(
    final: Mapping[str, Any], config: ReliabilityConfig
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    terminal = (
        final["completed_operations"]
        + final["failed_operations"]
        + final["cancelled_operations"]
    )
    if final["accepted_operations"] != terminal:
        failures.append(
            _failure(
                "terminal_accounting",
                "accepted operations do not equal completed + failed + cancelled",
            )
        )
    if final["incorrect_results"] != 0:
        failures.append(
            _failure("incorrect_results", "one or more operations returned a wrong result")
        )
    if final["post_crash_success"] is not True:
        failures.append(
            _failure(
                "post_crash_success",
                "isolated work did not recover after the deliberate crash",
            )
        )
    if final["post_recycle_success"] is not True:
        failures.append(
            _failure(
                "post_recycle_success",
                "isolated work did not succeed after the recycling boundary",
            )
        )

    engine = final["final_engine"]
    for field in ("queued_tasks", "running_tasks", "active_tasks"):
        if engine[field] != 0:
            failures.append(
                _failure(field, f"final engine gauge {field} was {engine[field]}, not zero")
            )

    child_count = final["max_observed_child_count"]
    if child_count is None or child_count > config.max_processes:
        failures.append(
            _failure(
                "maximum_child_count",
                "observed child count was unavailable or exceeded max_processes",
            )
        )
    if final["shutdown_seconds"] > config.shutdown_grace_seconds:
        failures.append(
            _failure(
                "shutdown_grace",
                "shutdown exceeded the configured grace period",
            )
        )
    return failures


def _shutdown_with_grace(
    pyroxide_facade: Any,
    state: _RunState,
    grace_seconds: float,
) -> tuple[float, bool]:
    shutdown_error: list[Exception] = []

    def shutdown() -> None:
        try:
            pyroxide_facade.shutdown(wait=True)
        except Exception as error:
            shutdown_error.append(error)

    started = time.monotonic()
    shutdown_thread = threading.Thread(
        target=shutdown,
        name="pyroxide-reliability-shutdown",
        daemon=True,
    )
    shutdown_thread.start()
    shutdown_thread.join(timeout=grace_seconds)
    elapsed = time.monotonic() - started
    if shutdown_thread.is_alive():
        state.record_assertion_failure(
            "shutdown_timeout",
            "shutdown(wait=True) did not return within the shutdown grace period",
        )
        return elapsed, False
    if shutdown_error:
        error = shutdown_error[0]
        state.record_assertion_failure(type(error).__name__, str(error))
        return elapsed, False
    return elapsed, True


def run_reliability(
    config: ReliabilityConfig,
    *,
    pyroxide_facade: Any | None = None,
    sampler: Any | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run duration-bounded scenarios, append observations, and return a summary."""
    previous_environment = _configure_pyroxide(config)
    try:
        return _run_reliability_configured(
            config,
            pyroxide_facade=pyroxide_facade,
            sampler=sampler,
            monotonic=monotonic,
            sleep=sleep,
        )
    finally:
        _restore_pyroxide_environment(previous_environment)


def _run_reliability_configured(
    config: ReliabilityConfig,
    *,
    pyroxide_facade: Any | None = None,
    sampler: Any | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    environment = asdict(collect_environment())
    if pyroxide_facade is None:
        pyroxide_facade = importlib.import_module("pyroxide")
    sampler = sampler or _default_sampler()

    identity_task = pyroxide_facade.task(_identity)
    isolated_identity_task = pyroxide_facade.task(_isolated_identity, isolated=True)
    crash_task = pyroxide_facade.task(_crash_worker, isolated=True)
    slow_task = pyroxide_facade.task(_sleep_then_identity)

    recycle_target = _recycle_target(config)
    state = _RunState(recycle_target=recycle_target)
    configuration = asdict(config)
    configuration["output"] = str(config.output)
    configuration["summary"] = str(config.summary)
    metadata = {
        "record_type": "metadata",
        "seed": config.seed,
        "configuration": configuration,
        "start_timestamp_utc": environment["timestamp_utc"],
        "environment": environment,
    }
    records: list[dict[str, Any]] = [metadata]
    _append_jsonl(config.output, metadata)

    started = monotonic()
    deadline = started + config.duration_seconds
    cycle_count = _planned_cycles(config)
    cycle_condition = threading.Condition()
    stop = threading.Event()

    def coordinate() -> None:
        try:
            for cycle in range(cycle_count):
                cycle_deadline = started + cycle * config.duration_seconds / cycle_count
                with cycle_condition:
                    while monotonic() < cycle_deadline and not stop.is_set():
                        cycle_condition.wait(
                            timeout=max(0.0, cycle_deadline - monotonic())
                        )
                if stop.is_set():
                    return
                submissions = recycle_target if cycle == 0 else 1
                _run_scenario_cycle(
                    cycle=cycle,
                    recycle_submissions=submissions,
                    identity_task=identity_task,
                    isolated_identity_task=isolated_identity_task,
                    crash_task=crash_task,
                    slow_task=slow_task,
                    pyroxide_facade=pyroxide_facade,
                    config=config,
                    state=state,
                    monotonic=monotonic,
                    deadline=deadline,
                    drain_deadline=deadline + config.shutdown_grace_seconds,
                    stop=stop,
                )
        except Exception as error:
            state.record_assertion_failure(type(error).__name__, str(error))

    coordinator = threading.Thread(
        target=coordinate,
        name="pyroxide-reliability-coordinator",
        daemon=True,
    )
    coordinator.start()

    try:
        sample_deadline = started
        while sample_deadline <= deadline:
            remaining = sample_deadline - monotonic()
            if remaining > 0:
                sleep(remaining)
            elapsed = min(config.duration_seconds, max(0.0, monotonic() - started))
            sample = _sample_record(
                elapsed_seconds=elapsed,
                pyroxide_facade=pyroxide_facade,
                sampler=sampler,
                state=state,
            )
            records.append(sample)
            _append_jsonl(config.output, sample)
            with cycle_condition:
                cycle_condition.notify_all()
            sample_deadline += config.sample_interval_seconds

        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(remaining)
    except Exception as error:
        state.record_assertion_failure(type(error).__name__, str(error))
    finally:
        stop.set()
        with cycle_condition:
            cycle_condition.notify_all()
        coordinator.join(timeout=config.shutdown_grace_seconds)

    shutdown_seconds, shutdown_completed = _shutdown_with_grace(
        pyroxide_facade,
        state,
        config.shutdown_grace_seconds,
    )
    if coordinator.is_alive():
        coordinator.join(timeout=config.shutdown_grace_seconds)
    if coordinator.is_alive():
        state.record_assertion_failure(
            "coordinator_shutdown",
            "scenario coordinator remained active after bounded shutdown",
        )
    try:
        _resource_snapshot(sampler, state)
    except Exception as error:
        state.record_assertion_failure(type(error).__name__, str(error))
    if shutdown_completed and not coordinator.is_alive():
        try:
            final_engine = _engine_snapshot(pyroxide_facade)
        except Exception as error:
            state.record_assertion_failure(type(error).__name__, str(error))
            final_engine = {field: -1 for field in _ENGINE_FIELDS}
    else:
        final_engine = {field: -1 for field in _ENGINE_FIELDS}
    final_values, final_latencies = state.freeze_and_take_final()
    final: dict[str, Any] = {
        "record_type": "final",
        "timestamp_utc": _utc_timestamp(),
        "elapsed_seconds": float(monotonic() - started),
        **final_values,
        "latency_seconds": final_latencies,
        "shutdown_seconds": float(max(0.0, shutdown_seconds)),
        "final_engine": final_engine,
    }

    failures = state.assertion_failures()
    failures.extend(_validate_final(final, config))
    final["assertion_failure_count"] = len(failures)
    for failure in failures:
        records.append(failure)
        _append_jsonl(config.output, failure)
    records.append(final)
    _append_jsonl(config.output, final)
    return summarize_observations(records, config)


def _numeric_values(values: Sequence[object]) -> list[float]:
    return [
        float(value)
        for value in values
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    ]


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _resource_value(record: Mapping[str, object], field: str) -> float | None:
    resources = record.get("resources")
    if not isinstance(resources, Mapping):
        return None
    value = resources.get(field)
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    ):
        return float(value)
    return None


def _window_resource_summary(records: Sequence[Mapping[str, object]]) -> dict[str, float | None]:
    rss_values = [
        value
        for record in records
        if (value := _resource_value(record, "rss_bytes")) is not None
    ]
    descriptor_values = [
        value
        for record in records
        if (value := _resource_value(record, "descriptor_count")) is not None
    ]
    return {
        "rss_median_bytes": _median(rss_values),
        "descriptor_median": _median(descriptor_values),
    }


def summarize_observations(
    records: Sequence[Mapping[str, object]], config: ReliabilityConfig
) -> dict[str, Any]:
    """Summarize canonical reliability records without executing live work."""
    del config
    samples = [record for record in records if record.get("record_type") == "sample"]
    final_records = [
        record for record in records if record.get("record_type") == "final"
    ]
    failures = [
        {key: value for key, value in record.items() if key != "record_type"}
        for record in records
        if record.get("record_type") == "assertion_failure"
    ]
    latency_records = [
        record
        for record in records
        if record.get("record_type") in {"sample", "final"}
    ]
    latencies = _numeric_values(
        [
            latency
            for record in latency_records
            for latency in (
                record.get("latency_seconds")
                if isinstance(record.get("latency_seconds"), list)
                else []
            )
        ]
    )
    child_counts = [
        value
        for record in samples
        if (value := _resource_value(record, "child_count")) is not None
    ]
    window_size = max(1, len(samples) // 5)
    summary = {
        "sample_count": len(samples),
        "latency_seconds": {
            "count": len(latencies),
            "median": _median(latencies),
            "p95": _nearest_rank_percentile(latencies, 0.95),
            "maximum": max(latencies) if latencies else None,
        },
        "resources": {
            "first_window": _window_resource_summary(samples[:window_size]),
            "last_window": _window_resource_summary(samples[-window_size:]),
            "maximum_child_count": max(child_counts) if child_counts else None,
        },
        "assertion_failures": failures,
        "ok": not failures,
    }
    if final_records:
        summary["final"] = {
            key: value
            for key, value in final_records[-1].items()
            if key != "record_type"
        }
    return summary


def write_summary_exclusive(path: Path, summary: Mapping[str, object]) -> None:
    """Fill an empty summary artifact that was exclusively reserved for this run."""
    reservation_key = path.resolve()
    reserved_identity = _RESERVED_SUMMARIES.get(reservation_key)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        if reserved_identity is None:
            raise FileExistsError(
                f"refusing to overwrite existing summary: {path}"
            ) from error
        descriptor = os.open(path, os.O_WRONLY)
        status = os.fstat(descriptor)
        if _file_identity(status) != reserved_identity or status.st_size:
            os.close(descriptor)
            raise FileExistsError(f"refusing to overwrite existing summary: {path}")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
            json.dump(summary, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _RESERVED_SUMMARIES.pop(reservation_key, None)


def _append_jsonl(path: Path, record: Mapping[str, object]) -> None:
    encoded = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    key = _reservation_key(path)
    reserved_identity = _RESERVED_OUTPUTS.get(key)
    flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if reserved_identity is not None:
            raise FileExistsError(
                f"refusing to overwrite existing output: {path}"
            ) from error
        raise
    try:
        status = os.fstat(descriptor)
        if (
            reserved_identity is not None
            and _file_identity(status) != reserved_identity
        ):
            raise FileExistsError(f"refusing to overwrite existing output: {path}")
        with os.fdopen(descriptor, "ab", closefd=False) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(descriptor)
        if reserved_identity is not None:
            new_status = os.fstat(descriptor)
            _RESERVED_OUTPUTS[key] = _file_identity(new_status)
    finally:
        os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    """Reserve artifacts, invoke the controller, and map summary health to exit status."""
    config = parse_args(argv)
    reserve_outputs(config.output, config.summary)
    try:
        try:
            summary = run_reliability(config)
        except Exception as error:
            failure = {
                "record_type": "assertion_failure",
                "name": "unexpected_exception",
                "message": str(error),
            }
            _append_jsonl(config.output, failure)
            summary = summarize_observations([failure], config)
        write_summary_exclusive(config.summary, summary)
        return 0 if summary.get("ok") is True else 1
    finally:
        _RESERVED_OUTPUTS.pop(_reservation_key(config.output), None)


if __name__ == "__main__":  # pragma: no cover - exercised by the script entry point.
    raise SystemExit(main())
