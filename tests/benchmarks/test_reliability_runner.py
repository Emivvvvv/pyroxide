"""Contract tests for the duration-aware reliability runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

from examples.benchmarks import reliability_runner
from examples.benchmarks.environment import ProcessTreeSample

ROOT = Path(__file__).resolve().parents[2]


class FakeClock:
    """Advance deterministic monotonic time whenever the controller sleeps."""

    def __init__(self) -> None:
        self.current = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds
        time.sleep(0.005)


class FakeSampler:
    """Return stable process-tree evidence without touching the host."""

    def __init__(self) -> None:
        self.sample_count = 0

    def sample(self) -> ProcessTreeSample:
        self.sample_count += 1
        return ProcessTreeSample(
            cpu_time_seconds=0.25,
            rss_bytes=1_024,
            voluntary_context_switches=3,
            involuntary_context_switches=1,
            file_descriptors=7,
            children_total=2,
            children_started=0,
            children_exited=0,
        )


class FailingSampler:
    """Expose whether controller cleanup survives a sampling exception."""

    def sample(self) -> ProcessTreeSample:
        raise RuntimeError("sampling fixture error")


class FakeHandle:
    """Mirror the status, cancellation, and result surface of a task handle."""

    def __init__(
        self,
        facade: FakePyroxide,
        *,
        value: int,
        status: str,
        crash: bool = False,
    ) -> None:
        self._facade = facade
        self._value = value
        self._status = status
        self._crash = crash

    @property
    def status(self) -> str:
        return self._status

    def cancel(self) -> bool:
        if self._status != "Pending":
            return False
        self._status = "Cancelled"
        self._facade.cancelled_tasks += 1
        return True

    def result(self) -> int:
        if self._status == "Cancelled":
            raise RuntimeError("Task cancelled")
        if self._crash:
            self._status = "Failed"
            self._facade.failed_tasks += 1
            raise RuntimeError("Worker process closed connection (crashed/EOF) on read")
        self._status = "Completed"
        self._facade.completed_tasks += 1
        return self._value


class FakePyroxide:
    """Deterministic facade with the same four dependencies used in production."""

    def __init__(self) -> None:
        self.handles: list[FakeHandle] = []
        self.submitted_tasks = 0
        self.rejected_tasks = 0
        self.completed_tasks = 0
        self.failed_tasks = 0
        self.cancelled_tasks = 0
        self.shutdown_calls: list[bool] = []

    def task(self, function: Any, *, isolated: bool = False) -> Any:
        del isolated

        def submit(payload: Any) -> FakeHandle:
            active_slow = [
                handle
                for handle in self.handles
                if handle.status in {"Pending", "Running"}
                and getattr(handle, "_slow", False)
            ]
            if function.__name__ == "_sleep_then_identity" and len(active_slow) >= 6:
                self.rejected_tasks += 1
                raise BufferError("bounded queue is full")

            if function.__name__ == "_sleep_then_identity":
                _, value = payload
                status = "Running" if len(active_slow) < 2 else "Pending"
            else:
                value = payload
                status = "Running"
            handle = FakeHandle(
                self,
                value=value,
                status=status,
                crash=function.__name__ == "_crash_worker",
            )
            handle._slow = function.__name__ == "_sleep_then_identity"
            self.handles.append(handle)
            self.submitted_tasks += 1
            return handle

        return submit

    def stats(self) -> dict[str, int]:
        queued = sum(handle.status == "Pending" for handle in self.handles)
        running = sum(handle.status == "Running" for handle in self.handles)
        active = queued + running
        return {
            "worker_count": 2,
            "max_processes": 2,
            "queue_capacity": 4,
            "queued_tasks": queued,
            "running_tasks": running,
            "active_tasks": active,
            "submitted_tasks": self.submitted_tasks,
            "rejected_tasks": self.rejected_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "cancelled_tasks": self.cancelled_tasks,
        }

    @contextmanager
    def scoped(self, *, queue_timeout_ms: int) -> Iterator[None]:
        assert queue_timeout_ms == 0
        yield

    def shutdown(self, *, wait: bool) -> None:
        self.shutdown_calls.append(wait)


class FailingScenarioPyroxide(FakePyroxide):
    """Raise one unexpected scenario error after an accepted slow operation."""

    def task(self, function: Any, *, isolated: bool = False) -> Any:
        submit = super().task(function, isolated=isolated)
        calls = 0

        def maybe_fail(payload: Any) -> FakeHandle:
            nonlocal calls
            calls += 1
            if function.__name__ == "_sleep_then_identity" and calls == 2:
                raise ValueError("scenario fixture error")
            return submit(payload)

        return maybe_fail


class UnrelatedFailureHandle(FakeHandle):
    def result(self) -> int:
        self._status = "Failed"
        self._facade.failed_tasks += 1
        raise ValueError("serialization fixture error")


class UnrelatedCrashPyroxide(FakePyroxide):
    """Make the deliberate-crash submission fail for an unrelated reason."""

    def task(self, function: Any, *, isolated: bool = False) -> Any:
        if function.__name__ != "_crash_worker":
            return super().task(function, isolated=isolated)

        def submit(payload: int) -> UnrelatedFailureHandle:
            handle = UnrelatedFailureHandle(
                self,
                value=payload,
                status="Running",
            )
            self.handles.append(handle)
            self.submitted_tasks += 1
            return handle

        return submit


class TimeoutThenSuccessHandle(FakeHandle):
    """Model Pyroxide's non-terminal timeout followed by a real result."""

    def __init__(
        self,
        *args: Any,
        clock: FakeClock,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._clock = clock
        self.result_calls = 0
        self.timeouts: list[float | None] = []

    def result(self, timeout_sec: float | None = None) -> int:
        self.timeouts.append(timeout_sec)
        self.result_calls += 1
        if self.result_calls == 1:
            self._clock.current += 100
            raise TimeoutError("task is still running")
        return super().result()


class TimeoutThenSuccessPyroxide(FakePyroxide):
    def __init__(self, clock: FakeClock) -> None:
        super().__init__()
        self._clock = clock
        self.timeout_handle: TimeoutThenSuccessHandle | None = None

    def task(self, function: Any, *, isolated: bool = False) -> Any:
        submit = super().task(function, isolated=isolated)

        def maybe_timeout(payload: Any) -> FakeHandle:
            if function.__name__ == "_identity" and self.timeout_handle is None:
                handle = TimeoutThenSuccessHandle(
                    self,
                    value=payload,
                    status="Running",
                    clock=self._clock,
                )
                self.handles.append(handle)
                self.submitted_tasks += 1
                self.timeout_handle = handle
                return handle
            return submit(payload)

        return maybe_timeout


class TerminalAfterTimeoutHandle(FakeHandle):
    """Transition to a terminal status between timeout and status inspection."""

    def __init__(self, *args: Any, terminal_status: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._terminal_status = terminal_status
        self.result_calls = 0

    def result(self, timeout_sec: float | None = None) -> int:
        del timeout_sec
        self.result_calls += 1
        if self.result_calls == 1:
            self._status = self._terminal_status
            raise TimeoutError("task crossed its terminal boundary")
        if self._terminal_status == "Failed":
            self._facade.failed_tasks += 1
            raise ValueError("terminal fixture error")
        return super().result()


class TerminalAfterTimeoutPyroxide(FakePyroxide):
    def __init__(self, terminal_status: str) -> None:
        super().__init__()
        self._terminal_status = terminal_status
        self.transition_handle: TerminalAfterTimeoutHandle | None = None

    def task(self, function: Any, *, isolated: bool = False) -> Any:
        submit = super().task(function, isolated=isolated)

        def maybe_transition(payload: Any) -> FakeHandle:
            if function.__name__ == "_identity" and self.transition_handle is None:
                handle = TerminalAfterTimeoutHandle(
                    self,
                    value=payload,
                    status="Running",
                    terminal_status=self._terminal_status,
                )
                self.handles.append(handle)
                self.submitted_tasks += 1
                self.transition_handle = handle
                return handle
            return submit(payload)

        return maybe_transition


class BadCancelledHandle(FakeHandle):
    """A cancellation request succeeded, but result reports an engine defect."""

    def result(self, timeout_sec: float | None = None) -> int:
        del timeout_sec
        self._status = "Failed"
        self._facade.failed_tasks += 1
        raise RuntimeError("IPC fixture error")


class BadCancellationPyroxide(FakePyroxide):
    def task(self, function: Any, *, isolated: bool = False) -> Any:
        submit = super().task(function, isolated=isolated)
        pending_count = 0

        def bad_last_pending(payload: Any) -> FakeHandle:
            nonlocal pending_count
            handle = submit(payload)
            if function.__name__ == "_sleep_then_identity" and handle.status == "Pending":
                pending_count += 1
                if pending_count == 4:
                    replacement = BadCancelledHandle(
                        self,
                        value=handle._value,
                        status="Pending",
                    )
                    replacement._slow = True
                    self.handles[-1] = replacement
                    return replacement
            return handle

        return bad_last_pending


class ReleasableBlockingHandle(FakeHandle):
    def __init__(self, *args: Any, released: threading.Event, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._released = released

    def result(self) -> int:
        self._released.wait()
        time.sleep(0.02)
        return super().result()


class CoordinatorReleasePyroxide(FakePyroxide):
    """Shutdown releases a coordinator blocked in its first accepted result."""

    def __init__(self) -> None:
        super().__init__()
        self.released = threading.Event()
        self.created_blocker = False

    def task(self, function: Any, *, isolated: bool = False) -> Any:
        submit = super().task(function, isolated=isolated)

        def maybe_block(payload: Any) -> FakeHandle:
            if function.__name__ == "_identity" and not self.created_blocker:
                self.created_blocker = True
                handle = ReleasableBlockingHandle(
                    self,
                    value=payload,
                    status="Running",
                    released=self.released,
                )
                self.handles.append(handle)
                self.submitted_tasks += 1
                return handle
            return submit(payload)

        return maybe_block

    def shutdown(self, *, wait: bool) -> None:
        super().shutdown(wait=wait)
        self.released.set()


class BlockingShutdownPyroxide(FakePyroxide):
    def __init__(self) -> None:
        super().__init__()
        self.release_shutdown = threading.Event()

    def shutdown(self, *, wait: bool) -> None:
        self.shutdown_calls.append(wait)
        self.release_shutdown.wait()


def test_cli_requires_positive_duration_and_interval(tmp_path: Path) -> None:
    """Non-positive timing values would make a timed run undefined."""
    with pytest.raises(SystemExit):
        reliability_runner.parse_args(
            [
                "--duration-seconds",
                "0",
                "--sample-interval-seconds",
                "1",
                "--output",
                str(tmp_path / "raw.jsonl"),
                "--summary",
                str(tmp_path / "summary.json"),
            ]
        )


def test_cli_rejects_interval_longer_than_duration(tmp_path: Path) -> None:
    """A sample interval must fit within the requested observation period."""
    with pytest.raises(SystemExit):
        reliability_runner.parse_args(
            [
                "--duration-seconds",
                "2",
                "--sample-interval-seconds",
                "3",
                "--output",
                str(tmp_path / "raw.jsonl"),
                "--summary",
                str(tmp_path / "summary.json"),
            ]
        )


@pytest.mark.parametrize("value", ["nan", "inf"])
def test_cli_rejects_non_finite_timing_values(tmp_path: Path, value: str) -> None:
    """A non-finite duration cannot bound a reliability run."""
    with pytest.raises(SystemExit):
        reliability_runner.parse_args(
            [
                "--duration-seconds",
                value,
                "--sample-interval-seconds",
                "1",
                "--output",
                str(tmp_path / "raw.jsonl"),
                "--summary",
                str(tmp_path / "summary.json"),
            ]
        )


@pytest.mark.parametrize("existing", ["output", "summary"])
def test_cli_refuses_to_overwrite_either_artifact(
    tmp_path: Path, existing: str
) -> None:
    """Existing raw observations and summaries are both immutable artifacts."""
    output = tmp_path / "raw.jsonl"
    summary = tmp_path / "summary.json"
    {"output": output, "summary": summary}[existing].write_text("{}\n")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        reliability_runner.reserve_outputs(output, summary)


def test_output_reservation_rolls_back_raw_file_when_summary_creation_fails(
    tmp_path: Path,
) -> None:
    """A failed second reservation must not leave a misleading empty raw artifact."""
    output = tmp_path / "raw.jsonl"
    summary = tmp_path / "missing" / "summary.json"

    with pytest.raises(FileNotFoundError):
        reliability_runner.reserve_outputs(output, summary)

    assert not output.exists()


def test_summary_writer_refuses_an_existing_or_replaced_summary(tmp_path: Path) -> None:
    """Summary writing must never replace an artifact it did not reserve."""
    output = tmp_path / "raw.jsonl"
    summary = tmp_path / "summary.json"
    reliability_runner.reserve_outputs(output, summary)
    summary.unlink()
    summary.write_text('{"existing": true}\n', encoding="utf-8")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        reliability_runner.write_summary_exclusive(summary, {"ok": True})

    assert summary.read_text(encoding="utf-8") == '{"existing": true}\n'


def test_deterministic_summary_uses_nearest_rank_and_window_medians(
    tmp_path: Path,
) -> None:
    """Changing rank or window rules would make this fixed soak summary drift."""
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=4.0,
        sample_interval_seconds=1.0,
        output=tmp_path / "raw.jsonl",
        summary=tmp_path / "summary.json",
    )
    records = [
        {
            "record_type": "sample",
            "latency_seconds": [0.01],
            "resources": {
                "rss_bytes": 150,
                "descriptor_count": 11,
                "child_count": 1,
            },
            "engine": {"submitted": 1, "completed": 1},
        },
        {
            "record_type": "sample",
            "latency_seconds": [0.02],
            "resources": {
                "rss_bytes": 250,
                "descriptor_count": 12,
                "child_count": 2,
            },
            "engine": {"submitted": 2, "completed": 2},
        },
        {
            "record_type": "sample",
            "latency_seconds": [0.03],
            "resources": {
                "rss_bytes": 300,
                "descriptor_count": 14,
                "child_count": 2,
            },
            "engine": {"submitted": 3, "completed": 3},
        },
        {
            "record_type": "sample",
            "latency_seconds": [0.04],
            "resources": {
                "rss_bytes": 350,
                "descriptor_count": 15,
                "child_count": 1,
            },
            "engine": {"submitted": 4, "completed": 4},
        },
        {"record_type": "metadata", "seed": 1729},
    ]

    summary = reliability_runner.summarize_observations(records, config)

    assert summary["sample_count"] == 4
    assert summary["latency_seconds"] == {
        "count": 4,
        "median": 0.025,
        "p95": 0.04,
        "maximum": 0.04,
    }
    assert summary["resources"]["first_window"]["rss_median_bytes"] == 150
    assert summary["resources"]["last_window"]["rss_median_bytes"] == 350
    assert summary["resources"]["first_window"]["descriptor_median"] == 11
    assert summary["resources"]["last_window"]["descriptor_median"] == 15
    assert summary["resources"]["maximum_child_count"] == 2
    assert summary["assertion_failures"] == []
    assert summary["ok"] is True


def test_summary_ignores_non_finite_measurements(tmp_path: Path) -> None:
    """Non-finite measurements must not leak into portable JSON output."""
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=1.0,
        sample_interval_seconds=1.0,
        output=tmp_path / "raw.jsonl",
        summary=tmp_path / "summary.json",
    )
    records = [
        {
            "record_type": "sample",
            "latency_seconds": [float("nan"), float("inf")],
            "resources": {
                "rss_bytes": float("nan"),
                "descriptor_count": float("inf"),
                "child_count": float("nan"),
            },
        }
    ]

    summary = reliability_runner.summarize_observations(records, config)

    assert summary["latency_seconds"] == {
        "count": 0,
        "median": None,
        "p95": None,
        "maximum": None,
    }
    assert summary["resources"] == {
        "first_window": {"rss_median_bytes": None, "descriptor_median": None},
        "last_window": {"rss_median_bytes": None, "descriptor_median": None},
        "maximum_child_count": None,
    }


def test_scenario_controller_records_terminal_accounting_and_recovery(
    tmp_path: Path,
) -> None:
    """Dropping any scenario terminal state would break accepted-operation accounting."""
    output = tmp_path / "scenario.jsonl"
    output.touch()
    clock = FakeClock()
    facade = FakePyroxide()
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=3.0,
        sample_interval_seconds=1.0,
        output=output,
        summary=tmp_path / "scenario.summary.json",
    )

    summary = reliability_runner.run_reliability(
        config,
        pyroxide_facade=facade,
        sampler=FakeSampler(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    metadata = next(
        record for record in records if record["record_type"] == "metadata"
    )
    final = next(record for record in records if record["record_type"] == "final")
    assert metadata["seed"] == 1729
    assert metadata["configuration"] == {
        "duration_seconds": 3.0,
        "max_processes": 2,
        "max_tasks_per_worker": 100,
        "output": str(output),
        "queue_capacity": 4,
        "sample_interval_seconds": 1.0,
        "seed": 1729,
        "shutdown_grace_seconds": 10.0,
        "summary": str(config.summary),
        "workers": 2,
    }
    assert isinstance(metadata["start_timestamp_utc"], str)
    assert isinstance(metadata["environment"]["python_version"], str)
    assert isinstance(metadata["environment"]["os_name"], str)
    assert "pyro3" in metadata["environment"]["package_versions"]
    assert metadata["environment"]["argv"] == sys.argv
    assert "git_sha" in metadata["environment"]
    assert "git_dirty" in metadata["environment"]
    assert final["accepted_operations"] == (
        final["completed_operations"]
        + final["failed_operations"]
        + final["cancelled_operations"]
    )
    assert final["rejected_operations"] >= 1
    assert final["post_crash_success"] is True
    assert final["post_recycle_success"] is True
    assert final["incorrect_results"] == 0
    assert all(
        sample["engine"]["active_tasks"] >= 0
        for sample in records
        if sample["record_type"] == "sample"
    )
    assert facade.shutdown_calls == [True]
    assert summary["ok"] is True


def test_reliability_run_restores_process_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A library-style run must not leak engine settings into later work."""
    monkeypatch.setenv("PYROXIDE_WORKERS", "17")
    monkeypatch.delenv("PYROXIDE_QUEUE_CAPACITY", raising=False)
    output = tmp_path / "environment.jsonl"
    output.touch()
    clock = FakeClock()
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=3.0,
        sample_interval_seconds=1.0,
        output=output,
        summary=tmp_path / "environment.summary.json",
    )

    reliability_runner.run_reliability(
        config,
        pyroxide_facade=FakePyroxide(),
        sampler=FakeSampler(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert os.environ["PYROXIDE_WORKERS"] == "17"
    assert "PYROXIDE_QUEUE_CAPACITY" not in os.environ


def test_scenario_exception_preserves_its_type_and_message(tmp_path: Path) -> None:
    """Saturation cleanup must not mask the exception that interrupted a cycle."""
    output = tmp_path / "scenario-error.jsonl"
    output.touch()
    clock = FakeClock()
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=3.0,
        sample_interval_seconds=1.0,
        output=output,
        summary=tmp_path / "scenario-error.summary.json",
    )

    reliability_runner.run_reliability(
        config,
        pyroxide_facade=FailingScenarioPyroxide(),
        sampler=FakeSampler(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        record.get("record_type") == "assertion_failure"
        and record.get("name") == "ValueError"
        and record.get("message") == "scenario fixture error"
        for record in records
    )


def test_controller_shutdown_and_final_record_survive_sampling_exception(
    tmp_path: Path,
) -> None:
    """A failed sampler must not bypass engine shutdown or final failure capture."""
    output = tmp_path / "sampler-error.jsonl"
    output.touch()
    facade = FakePyroxide()
    clock = FakeClock()
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=3.0,
        sample_interval_seconds=1.0,
        output=output,
        summary=tmp_path / "sampler-error.summary.json",
    )

    summary = reliability_runner.run_reliability(
        config,
        pyroxide_facade=facade,
        sampler=FailingSampler(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert facade.shutdown_calls == [True]
    assert records[-1]["record_type"] == "final"
    assert any(
        record.get("name") == "RuntimeError"
        and record.get("message") == "sampling fixture error"
        for record in records
    )
    assert summary["ok"] is False


def test_unrelated_crash_failure_does_not_prove_recovery(tmp_path: Path) -> None:
    """Serialization or IPC errors must not masquerade as the deliberate crash."""
    output = tmp_path / "wrong-crash.jsonl"
    output.touch()
    clock = FakeClock()
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=3.0,
        sample_interval_seconds=1.0,
        output=output,
        summary=tmp_path / "wrong-crash.summary.json",
    )

    reliability_runner.run_reliability(
        config,
        pyroxide_facade=UnrelatedCrashPyroxide(),
        sampler=FakeSampler(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    final = next(record for record in records if record["record_type"] == "final")
    assert final["post_crash_success"] is False
    assert any(
        record.get("name") == "ValueError"
        and record.get("message") == "serialization fixture error"
        for record in records
    )


def test_nonterminal_timeout_is_retried_until_the_handle_completes(
    tmp_path: Path,
) -> None:
    """A timeout must not become a failed terminal operation or lose its handle."""
    output = tmp_path / "timeout.jsonl"
    output.touch()
    clock = FakeClock()
    facade = TimeoutThenSuccessPyroxide(clock)
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=3.0,
        sample_interval_seconds=1.0,
        output=output,
        summary=tmp_path / "timeout.summary.json",
    )

    summary = reliability_runner.run_reliability(
        config,
        pyroxide_facade=facade,
        sampler=FakeSampler(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    final = summary["final"]
    assert facade.timeout_handle is not None
    assert facade.timeout_handle.result_calls == 2
    assert facade.timeout_handle.timeouts[0] is not None
    assert facade.timeout_handle.timeouts[1] is None
    assert final["accepted_operations"] == (
        final["completed_operations"]
        + final["failed_operations"]
        + final["cancelled_operations"]
    )
    assert not any(
        failure["name"] == "TimeoutError"
        for failure in summary["assertion_failures"]
    )


@pytest.mark.parametrize(
    ("terminal_status", "expected_failure"),
    [
        ("Completed", None),
        ("Failed", ("ValueError", "terminal fixture error")),
    ],
)
def test_timeout_terminal_transition_retrieves_the_real_outcome(
    tmp_path: Path,
    terminal_status: str,
    expected_failure: tuple[str, str] | None,
) -> None:
    """A status race after timeout must still consume the real terminal result."""
    output = tmp_path / f"timeout-{terminal_status.lower()}.jsonl"
    output.touch()
    clock = FakeClock()
    facade = TerminalAfterTimeoutPyroxide(terminal_status)
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=3.0,
        sample_interval_seconds=1.0,
        output=output,
        summary=tmp_path / f"timeout-{terminal_status.lower()}.summary.json",
    )

    summary = reliability_runner.run_reliability(
        config,
        pyroxide_facade=facade,
        sampler=FakeSampler(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert facade.transition_handle is not None
    assert facade.transition_handle.result_calls == 2
    assert not any(
        failure["name"] == "TimeoutError"
        for failure in summary["assertion_failures"]
    )
    if expected_failure is None:
        assert not any(
            failure["name"] == "ValueError"
            for failure in summary["assertion_failures"]
        )
    else:
        assert any(
            (failure["name"], failure["message"]) == expected_failure
            for failure in summary["assertion_failures"]
        )
    final = summary["final"]
    assert final["accepted_operations"] == (
        final["completed_operations"]
        + final["failed_operations"]
        + final["cancelled_operations"]
    )


def test_unrelated_error_after_cancel_request_is_not_counted_as_cancelled(
    tmp_path: Path,
) -> None:
    """Only the documented cancelled status/error proves a cancelled terminal."""
    output = tmp_path / "bad-cancellation.jsonl"
    output.touch()
    clock = FakeClock()
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=3.0,
        sample_interval_seconds=1.0,
        output=output,
        summary=tmp_path / "bad-cancellation.summary.json",
    )

    summary = reliability_runner.run_reliability(
        config,
        pyroxide_facade=BadCancellationPyroxide(),
        sampler=FakeSampler(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    final = summary["final"]
    assert final["cancelled_operations"] == 0
    assert any(
        failure["name"] == "RuntimeError"
        and failure["message"] == "IPC fixture error"
        for failure in summary["assertion_failures"]
    )


def test_shutdown_unblocks_coordinator_before_final_accounting_snapshot(
    tmp_path: Path,
) -> None:
    """Final accounting must wait for a coordinator released by shutdown."""
    output = tmp_path / "coordinator-release.jsonl"
    output.touch()
    facade = CoordinatorReleasePyroxide()
    clock = FakeClock()
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=0.1,
        sample_interval_seconds=0.1,
        output=output,
        summary=tmp_path / "coordinator-release.summary.json",
        shutdown_grace_seconds=0.05,
    )

    summary = reliability_runner.run_reliability(
        config,
        pyroxide_facade=facade,
        sampler=FakeSampler(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    wait_deadline = time.monotonic() + 1
    while any(
        thread.name == "pyroxide-reliability-coordinator" and thread.is_alive()
        for thread in threading.enumerate()
    ):
        assert time.monotonic() < wait_deadline
        time.sleep(0.001)

    final = summary["final"]
    assert final["accepted_operations"] == (
        final["completed_operations"]
        + final["failed_operations"]
        + final["cancelled_operations"]
    )


def test_shutdown_timeout_emits_failure_and_final_without_hanging(
    tmp_path: Path,
) -> None:
    """A deadlocked shutdown must be bounded and still publish an unhealthy final."""
    output = tmp_path / "shutdown-timeout.jsonl"
    output.touch()
    facade = BlockingShutdownPyroxide()
    clock = FakeClock()
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=0.05,
        sample_interval_seconds=0.05,
        output=output,
        summary=tmp_path / "shutdown-timeout.summary.json",
        shutdown_grace_seconds=0.05,
    )
    result: dict[str, Any] = {}

    def invoke() -> None:
        result.update(
            reliability_runner.run_reliability(
                config,
                pyroxide_facade=facade,
                sampler=FakeSampler(),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
        )

    controller = threading.Thread(target=invoke)
    controller.start()
    controller.join(timeout=0.5)
    returned_within_bound = not controller.is_alive()
    facade.release_shutdown.set()
    controller.join(timeout=1)

    assert returned_within_bound
    assert not controller.is_alive()
    assert result["ok"] is False
    assert any(
        failure["name"] == "shutdown_timeout"
        for failure in result["assertion_failures"]
    )
    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["record_type"] == "final"


def test_recycling_targets_one_short_and_two_official_run_boundaries(
    tmp_path: Path,
) -> None:
    """Duration profiles must cross the requested boundaries without excess work."""
    short = reliability_runner.ReliabilityConfig(
        duration_seconds=3.0,
        sample_interval_seconds=1.0,
        output=tmp_path / "short.jsonl",
        summary=tmp_path / "short.summary.json",
    )
    below_official = reliability_runner.ReliabilityConfig(
        duration_seconds=59.0,
        sample_interval_seconds=1.0,
        output=tmp_path / "below-official.jsonl",
        summary=tmp_path / "below-official.summary.json",
    )
    official_threshold = reliability_runner.ReliabilityConfig(
        duration_seconds=60.0,
        sample_interval_seconds=1.0,
        output=tmp_path / "official-threshold.jsonl",
        summary=tmp_path / "official-threshold.summary.json",
    )
    rc_soak = reliability_runner.ReliabilityConfig(
        duration_seconds=5 * 60,
        sample_interval_seconds=60.0,
        output=tmp_path / "rc-soak.jsonl",
        summary=tmp_path / "rc-soak.summary.json",
    )

    assert reliability_runner._recycle_target(short) == 101
    assert reliability_runner._recycle_target(below_official) == 101
    assert reliability_runner._recycle_target(official_threshold) == 201
    assert reliability_runner._recycle_target(rc_soak) == 201


def test_sampler_uses_absolute_cadence_and_strict_sample_schema(
    tmp_path: Path,
) -> None:
    """A relative or incomplete sampler would make duration comparisons unreliable."""
    output = tmp_path / "samples.jsonl"
    output.touch()
    clock = FakeClock()
    sampler = FakeSampler()
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=3.0,
        sample_interval_seconds=1.0,
        output=output,
        summary=tmp_path / "samples.summary.json",
    )

    reliability_runner.run_reliability(
        config,
        pyroxide_facade=FakePyroxide(),
        sampler=sampler,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    samples = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["record_type"] == "sample"
    ]
    assert [sample["elapsed_seconds"] for sample in samples] == [0.0, 1.0, 2.0, 3.0]
    assert clock.sleeps == [1.0, 1.0, 1.0]
    assert sampler.sample_count >= len(samples)
    expected_engine = {
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
    }
    assert all(
        set(sample)
        == {
            "record_type",
            "timestamp_utc",
            "elapsed_seconds",
            "engine",
            "resources",
            "operations",
            "latency_seconds",
        }
        for sample in samples
    )
    assert all(set(sample["engine"]) == expected_engine for sample in samples)
    assert all(
        set(sample["resources"])
        == {"rss_bytes", "descriptor_count", "child_count"}
        for sample in samples
    )
    assert all(isinstance(sample["timestamp_utc"], str) for sample in samples)
    assert all(type(sample["elapsed_seconds"]) is float for sample in samples)
    assert all(
        all(type(value) is int for value in sample["engine"].values())
        for sample in samples
    )
    assert all(
        all(value is None or type(value) is int for value in sample["resources"].values())
        for sample in samples
    )
    assert all(isinstance(sample["operations"], dict) for sample in samples)
    assert all(
        isinstance(sample["latency_seconds"], list)
        and all(type(value) is float for value in sample["latency_seconds"])
        for sample in samples
    )


def test_process_table_fallback_excludes_its_own_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The optional-dependency fallback must not report its `ps` probe as a worker."""

    class FakeProbe:
        pid = 999
        returncode = 0

        def communicate(self, *, timeout: int) -> tuple[str, str]:
            assert timeout == 2
            return "10 1\n11 10\n12 11\n999 10\n", ""

    monkeypatch.setattr(
        reliability_runner.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProbe(),
    )

    children = reliability_runner._ProcessTableRoot(10).children(recursive=True)

    assert {child.pid for child in children} == {11, 12}


@pytest.mark.parametrize(
    ("name", "mutation"),
    [
        ("terminal_accounting", {"accepted_operations": 4}),
        ("incorrect_results", {"incorrect_results": 1}),
        ("post_crash_success", {"post_crash_success": False}),
        ("post_recycle_success", {"post_recycle_success": False}),
        ("queued_tasks", {"final_engine": {"queued_tasks": 1}}),
        ("running_tasks", {"final_engine": {"running_tasks": 1}}),
        ("active_tasks", {"final_engine": {"active_tasks": 1}}),
        ("maximum_child_count", {"max_observed_child_count": 3}),
        ("shutdown_grace", {"shutdown_seconds": 10.1}),
    ],
)
def test_final_invariants_reject_each_reliability_regression(
    tmp_path: Path, name: str, mutation: dict[str, Any]
) -> None:
    """Each final invariant must independently turn a corrupted run unhealthy."""
    config = reliability_runner.ReliabilityConfig(
        duration_seconds=3.0,
        sample_interval_seconds=1.0,
        output=tmp_path / "raw.jsonl",
        summary=tmp_path / "summary.json",
    )
    final: dict[str, Any] = {
        "accepted_operations": 3,
        "completed_operations": 1,
        "failed_operations": 1,
        "cancelled_operations": 1,
        "incorrect_results": 0,
        "post_crash_success": True,
        "post_recycle_success": True,
        "final_engine": {
            "queued_tasks": 0,
            "running_tasks": 0,
            "active_tasks": 0,
        },
        "max_observed_child_count": 2,
        "shutdown_seconds": 10.0,
    }
    for key, value in mutation.items():
        if key == "final_engine":
            final["final_engine"].update(value)
        else:
            final[key] = value

    failures = reliability_runner._validate_final(final, config)

    assert name in {failure["name"] for failure in failures}


def test_live_reliability_module_completes_with_clean_invariants(
    tmp_path: Path,
) -> None:
    """The canonical three-second profile must exercise the real engine end to end."""
    output = tmp_path / "integration.jsonl"
    summary_path = tmp_path / "integration.summary.json"
    command = [
        sys.executable,
        "-m",
        "examples.benchmarks.reliability_runner",
        "--duration-seconds",
        "3",
        "--sample-interval-seconds",
        "1",
        "--seed",
        "1729",
        "--output",
        str(output),
        "--summary",
        str(summary_path),
    ]
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("PYROXIDE_")
    }

    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    elapsed = time.perf_counter() - started

    assert completed.returncode == 0, completed.stderr
    assert 2.0 <= elapsed <= 5.0
    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert sum(record["record_type"] == "sample" for record in records) >= 3
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    final = summary["final"]
    assert summary["ok"] is True
    assert final["rejected_operations"] >= 1
    assert final["post_crash_success"] is True
    assert final["post_recycle_success"] is True
    assert final["accepted_operations"] == (
        final["completed_operations"]
        + final["failed_operations"]
        + final["cancelled_operations"]
    )
    assert final["final_engine"]["queued_tasks"] == 0
    assert final["final_engine"]["running_tasks"] == 0
    assert final["final_engine"]["active_tasks"] == 0
    assert summary["resources"]["maximum_child_count"] <= 2


def test_main_returns_nonzero_for_a_failed_reliability_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A controller assertion failure must make the command fail."""
    output = tmp_path / "raw.jsonl"
    summary = tmp_path / "summary.json"
    monkeypatch.setattr(
        reliability_runner,
        "run_reliability",
        lambda config: {"ok": False, "assertion_failures": [{"name": "fixture"}]},
    )

    result = reliability_runner.main(
        [
            "--duration-seconds",
            "2",
            "--sample-interval-seconds",
            "1",
            "--output",
            str(output),
            "--summary",
            str(summary),
        ]
    )

    assert result == 1
    assert json.loads(summary.read_text(encoding="utf-8"))["ok"] is False


def test_main_returns_zero_for_a_successful_reliability_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A clean controller result must make the command succeed."""
    output = tmp_path / "raw.jsonl"
    summary = tmp_path / "summary.json"
    monkeypatch.setattr(
        reliability_runner,
        "run_reliability",
        lambda config: {"ok": True, "assertion_failures": []},
    )

    result = reliability_runner.main(
        [
            "--duration-seconds",
            "2",
            "--sample-interval-seconds",
            "1",
            "--output",
            str(output),
            "--summary",
            str(summary),
        ]
    )

    assert result == 0
    assert json.loads(summary.read_text(encoding="utf-8"))["ok"] is True


def test_main_records_an_unexpected_controller_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unexpected controller error must remain visible in the raw stream."""
    output = tmp_path / "raw.jsonl"
    summary = tmp_path / "summary.json"

    def raise_fixture_error(config: reliability_runner.ReliabilityConfig) -> dict[str, object]:
        raise RuntimeError("fixture error")

    monkeypatch.setattr(reliability_runner, "run_reliability", raise_fixture_error)

    result = reliability_runner.main(
        [
            "--duration-seconds",
            "2",
            "--sample-interval-seconds",
            "1",
            "--output",
            str(output),
            "--summary",
            str(summary),
        ]
    )

    assert result == 1
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "message": "fixture error",
        "name": "unexpected_exception",
        "record_type": "assertion_failure",
    }
    assert json.loads(summary.read_text(encoding="utf-8"))["ok"] is False
