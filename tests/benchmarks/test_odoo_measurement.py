from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.odoo_benchmark import ledger_kernel
from examples.odoo_benchmark.addons.pyroxide_benchmark.services import (
    benchmarking,
    workloads,
)


def test_measure_matched_batches_verifies_results_and_reports_batch_samples() -> None:
    payloads = (b"a", b"bb")

    result = benchmarking.measure_matched_batches(
        payloads,
        inline=lambda payload: payload[::-1],
        process_batch=lambda values: tuple(value[::-1] for value in values),
        isolated_batch=lambda values: tuple(value[::-1] for value in values),
        repetitions=3,
        warmups=1,
    )

    assert result["semantics"] == "matched_batch_makespan"
    assert result["tasks_per_batch"] == 2
    assert len(result["samples"]["inline_python_seconds"]) == 3
    assert len(result["samples"]["process_pool_seconds"]) == 3
    assert len(result["samples"]["pyroxide_isolated_seconds"]) == 3
    assert result["summary"]["inline_python"]["median_seconds"] > 0
    assert result["summary"]["process_pool"]["median_seconds"] > 0
    assert result["summary"]["pyroxide_isolated"]["p95_seconds"] > 0


def test_measure_matched_batches_rejects_a_wrong_isolated_result() -> None:
    with pytest.raises(RuntimeError, match="isolated result"):
        benchmarking.measure_matched_batches(
            (b"a",),
            inline=lambda payload: payload,
            process_batch=lambda values: tuple(values),
            isolated_batch=lambda values: (b"wrong",),
            repetitions=1,
            warmups=0,
        )


def test_measure_matched_batches_rejects_a_wrong_process_pool_result() -> None:
    with pytest.raises(RuntimeError, match="process_pool result"):
        benchmarking.measure_matched_batches(
            (b"a",),
            inline=lambda payload: payload,
            process_batch=lambda values: (b"wrong",),
            isolated_batch=lambda values: tuple(values),
            repetitions=1,
            warmups=0,
        )


def test_write_result_is_append_safe(tmp_path: Path) -> None:
    output = tmp_path / "odoo.json"
    benchmarking.write_result(output, {"status": "ok"})

    assert json.loads(output.read_text(encoding="utf-8")) == {"status": "ok"}
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        benchmarking.write_result(output, {"status": "second"})


def test_batch_adapter_submits_all_payloads_before_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Handle:
        def __init__(self, value: bytes) -> None:
            self.value = value

        def result(self) -> bytes:
            return self.value

    class Task:
        def batch(self, values: list[bytes]) -> list[Handle]:
            return [Handle(value[::-1]) for value in values]

    monkeypatch.setattr(workloads, "initialize_pyroxide_after_fork", lambda: None)
    monkeypatch.setattr(workloads, "_isolated_audit", Task())

    assert workloads.compute_batch_with_pyroxide((b"a", b"bc")) == (b"a", b"cb")


def test_top_level_worker_uses_the_identical_ledger_kernel() -> None:
    payload = workloads.encode_ledger_payload([])

    assert ledger_kernel.compute_ledger_audit(payload) == workloads.compute_ledger_audit(
        payload
    )
    assert ledger_kernel.compute_ledger_audit.__module__ == (
        "examples.odoo_benchmark.ledger_kernel"
    )
