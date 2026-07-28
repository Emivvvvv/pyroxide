from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from examples.benchmarks import report, runner, worker
from examples.benchmarks.models import (
    BackendSpec,
    ExperimentManifest,
    ExperimentSpec,
    WorkloadSpec,
)


def steady_manifest(*, repetitions: int = 3) -> ExperimentManifest:
    return ExperimentManifest(
        workloads=(WorkloadSpec("payload", "payload_echo", 1, 8),),
        backends=(
            BackendSpec("alpha", "thread_pool", 1),
            BackendSpec("bravo", "thread_pool", 1),
            BackendSpec("charlie", "thread_pool", 1),
        ),
        experiments=tuple(
            ExperimentSpec(
                backend.id,
                "payload",
                backend.id,
                "steady_state",
                1729,
                repetitions,
            )
            for backend in (
                BackendSpec("alpha", "thread_pool", 1),
                BackendSpec("bravo", "thread_pool", 1),
                BackendSpec("charlie", "thread_pool", 1),
            )
        ),
    )


def test_seeded_plan_rotates_each_backend_through_every_position() -> None:
    """Dropping block rotation would unfairly keep one backend in a position."""
    cells = runner.plan_manifest(steady_manifest())

    assert cells == runner.plan_manifest(steady_manifest())
    assert len({cell.run_id for cell in cells}) == 9
    orders = [
        [cell.backend_id for cell in cells if cell.block_index == block]
        for block in range(3)
    ]
    assert all(sorted(order) == ["alpha", "bravo", "charlie"] for order in orders)
    assert all(
        Counter(order[position] for order in orders)
        == Counter({"alpha": 1, "bravo": 1, "charlie": 1})
        for position in range(3)
    )


def test_profile_plan_expands_worker_and_interpreter_matrices() -> None:
    """Ignoring profile matrices would publish one configuration as a study."""
    profile_path = (
        Path(__file__).parents[2]
        / "examples"
        / "benchmarks"
        / "manifests"
        / "exploratory.toml"
    )

    cells = runner.plan_profile(
        profile_path,
        physical_workers=8,
        interpreters={runner.current_interpreter_id(): sys.executable},
    )

    assert {cell.workers for cell in cells} == {1, 2, 4, 8}
    assert {cell.python_executable for cell in cells} == {sys.executable}
    assert all(cell.comparison_id for cell in cells)
    assert all(cell.environment_id for cell in cells)


def test_history_profile_requires_an_explicit_interpreter_for_every_version() -> None:
    """Silently running historical rows on one Python would invalidate them."""
    profile_path = (
        Path(__file__).parents[2]
        / "examples"
        / "benchmarks"
        / "manifests"
        / "python-history.toml"
    )

    with pytest.raises(ValueError, match="missing interpreter"):
        runner.plan_profile(
            profile_path,
            physical_workers=4,
            interpreters={runner.current_interpreter_id(): sys.executable},
        )


def test_generic_runner_rejects_reliability_profiles() -> None:
    """A one-shot throughput cell must never be reported as a timed soak."""
    profile_path = (
        Path(__file__).parents[2]
        / "examples"
        / "benchmarks"
        / "manifests"
        / "soak.toml"
    )

    with pytest.raises(ValueError, match="dedicated reliability runner"):
        runner.plan_profile(
            profile_path,
            interpreters={runner.current_interpreter_id(): sys.executable},
        )


def test_worker_command_uses_the_canonical_package_module() -> None:
    """Script-style imports create duplicate function identities for isolation."""
    cell = runner.plan_manifest(steady_manifest(repetitions=1))[0]

    command = runner._worker_command(cell)

    assert command[1:3] == ["-m", "examples.benchmarks.worker"]


def test_execution_refuses_to_overwrite_existing_raw_data(tmp_path: Path) -> None:
    """Replacing existing measurements would destroy an append-only raw record."""
    output = tmp_path / "raw.jsonl"
    output.write_text('{"existing": true}\n')

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        runner.run_execution(steady_manifest(repetitions=1), output)


def test_execution_creates_the_results_directory(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "raw.jsonl"

    assert runner.run_execution(
        ExperimentManifest(
            workloads=(WorkloadSpec("payload", "payload_echo", 1, 8),),
            backends=(BackendSpec("thread", "thread_pool", 1),),
            experiments=(),
        ),
        output,
    ) == 0
    assert output.is_file()


def test_execution_preserves_a_reportable_error_when_a_worker_fails(
    tmp_path: Path,
) -> None:
    """A failed subprocess must remain an explicit raw record."""
    output = tmp_path / "raw.jsonl"

    def fake_subprocess(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=7,
            stdout="not-json\n",
            stderr="worker failed",
        )

    result = runner.run_execution(
        steady_manifest(repetitions=1), output, subprocess_runner=fake_subprocess
    )

    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert result == 3
    assert len(records) == 3
    assert all(record["status"] == "error" for record in records)
    assert all(record["error"] == "worker failed" for record in records)


def test_worker_times_the_declared_workload_and_emits_a_report_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An identity-only result or lifecycle-only event cannot support conclusions."""
    submitted: list[tuple[str, tuple[bytes, ...]]] = []

    class FakeBackend:
        worker_count = 1
        start_method = "not_applicable"
        backend_name = "fixture"

        def submit_workload(
            self, workload_name: str, payloads: tuple[bytes, ...] | list[bytes]
        ) -> tuple[bytes, ...]:
            submitted.append((workload_name, tuple(payloads)))
            return tuple(
                worker.run_workload(workload_name, payload) for payload in payloads
            )

        def close(self) -> None:
            return None

    class FakeResources:
        peak_rss_bytes = 4096

        def __enter__(self) -> "FakeResources":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(worker, "create_backend", lambda spec: FakeBackend())
    monkeypatch.setattr(worker, "_PeakRSSMonitor", FakeResources)
    record = worker.execute_cell(
        {
            "run_id": "fixture-b0",
            "block_index": 0,
            "trial_index": 0,
            "experiment_id": "fixture",
            "backend_id": "thread",
            "workload_id": "cpu",
            "workload_kind": "python_cpu",
            "tasks_per_sample": 2,
            "payload_bytes": 1,
            "backend_kind": "thread_pool",
            "workers": 1,
        }
    )

    assert submitted and submitted[0][0] == "python_cpu"
    assert record["status"] == "ok"
    assert record["workload"] == "python_cpu"
    assert record["semantics"] == "steady_state_batch_makespan"
    assert record["throughput_tasks_per_second"] > 0
    assert record["artifact_hashes"]["workload"]
    assert "event" not in record


def test_runner_output_is_accepted_by_the_statistical_report(tmp_path: Path) -> None:
    """The runner and reporter must share one raw-data contract."""
    output = tmp_path / "raw.jsonl"

    def fake_subprocess(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        cell = json.loads(command[-1])
        record = {
            "schema_version": 1,
            "run_id": cell["run_id"],
            "experiment_id": "payload-w1",
            "workload": "payload_echo",
            "environment": "fixture-python",
            "semantics": "steady_state_batch_makespan",
            "artifact_hashes": {"workload": "a" * 64, "backend": "b" * 64},
            "artifact_checksum": "",
            "backend": cell["backend_id"],
            "block_index": cell["block_index"],
            "workers": 1,
            "status": "ok",
            "latency_seconds": 0.01,
            "throughput_tasks_per_second": 100.0,
            "peak_process_tree_rss_bytes": 1024,
        }
        record["artifact_checksum"] = report.artifact_checksum(
            record["artifact_hashes"]
        )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps(record) + "\n",
            stderr="",
        )

    assert (
        runner.run_execution(
            steady_manifest(repetitions=3),
            output,
            subprocess_runner=fake_subprocess,
        )
        == 0
    )

    summary = report.build_summary(output)
    assert set(summary["cells"]) == {"alpha", "bravo", "charlie"}


def test_plan_prints_zero_timed_trials_for_correctness_only_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Accidentally assigning timing repetitions to correctness-only runs is unsafe."""
    manifest = tmp_path / "smoke.toml"
    manifest.write_text(
        """schema_version = 1

[[workloads]]
id = "echo"
kind = "echo"
tasks_per_sample = 1
payload_bytes = 8

[[backends]]
id = "one"
kind = "thread_pool"
workers = 1

[[experiments]]
id = "smoke"
workload = "echo"
backend = "one"
profile = "correctness_only"
random_seed = 1
"""
    )

    assert runner.main(["--manifest", str(manifest), "--plan"]) == 0

    assert "timed_trials: 0" in capsys.readouterr().out


def test_validate_prints_the_resolved_matrix_without_execution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A validate-only command must expose the plan without entering execution."""
    manifest = tmp_path / "smoke.toml"
    manifest.write_text(
        """schema_version = 1

[[workloads]]
id = "echo"
kind = "echo"
tasks_per_sample = 1
payload_bytes = 8

[[backends]]
id = "one"
kind = "thread_pool"
workers = 1

[[experiments]]
id = "smoke"
workload = "echo"
backend = "one"
profile = "correctness_only"
random_seed = 1
"""
    )

    def must_not_execute(*args: object, **kwargs: object) -> int:
        raise AssertionError("validate must not execute a worker")

    monkeypatch.setattr(runner, "run_execution", must_not_execute)
    assert runner.main(["--manifest", str(manifest), "--validate"]) == 0

    output = capsys.readouterr().out
    assert "resolved matrix:" in output
    assert "timed_trials: 0" in output


@pytest.mark.parametrize(
    "script_name",
    [
        "benchmark.py",
        "benchmark_vs_alternatives.py",
        "benchmark_large_payload.py",
        "run_freethreaded_314.py",
    ],
)
def test_legacy_entry_points_import_without_removed_workload_helpers(
    script_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keeping the removed helper imports makes every old entry point unusable."""
    directory = Path(__file__).parents[2] / "examples" / "benchmarks"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location(f"legacy_{script_name}", directory / script_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert callable(module.main)
