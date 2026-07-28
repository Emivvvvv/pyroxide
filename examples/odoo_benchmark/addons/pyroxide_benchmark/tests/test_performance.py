"""Explicitly invoked Odoo-runtime measurements for the byte-only audit kernel."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import multiprocessing
import os
import platform
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

if importlib.util.find_spec("odoo") is None:  # pragma: no cover - host pytest only.
    import pytest

    pytest.skip(
        "Odoo TransactionCase tests require the pinned Odoo image",
        allow_module_level=True,
    )

from odoo.tests.common import TransactionCase, tagged

from examples.odoo_benchmark.ledger_kernel import compute_ledger_audit

from ..services.benchmarking import measure_matched_batches, write_result
from ..services.workloads import (
    compute_batch_with_pyroxide,
    encode_ledger_payload,
)


@tagged("post_install", "-at_install", "pyroxide_benchmark_performance")
class TestLedgerAuditPerformance(TransactionCase):
    def test_matched_compute_batches(self):
        payloads = _payloads(tasks=8, moves_per_payload=1_000)
        process_workers = 2
        process_start_method = "spawn"
        with ProcessPoolExecutor(
            max_workers=process_workers,
            mp_context=multiprocessing.get_context(process_start_method),
        ) as process_pool:
            result = measure_matched_batches(
                payloads,
                inline=compute_ledger_audit,
                process_batch=lambda values: tuple(
                    process_pool.map(compute_ledger_audit, values)
                ),
                isolated_batch=compute_batch_with_pyroxide,
                repetitions=30,
                warmups=3,
            )
        result.update(
            {
                "status": "ok",
                "comparison_scope": "odoo_compute_only_matched_batch",
                "executed_measurement_boundaries": ["compute_only"],
                "planned_measurement_boundaries": [
                    "orm_extraction_and_query_count",
                    "compute_only",
                    "orm_result_write",
                    "end_to_end_shell_invocation",
                    "authenticated_http_request",
                ],
                "odoo_release_label": os.environ["PYROXIDE_ODOO_RELEASE_LABEL"],
                "odoo_git_sha": os.environ["PYROXIDE_ODOO_GIT_SHA"],
                "python": sys.version,
                "platform": platform.platform(),
                "process_pool_start_method": process_start_method,
                "process_pool_workers": process_workers,
                "pyroxide_isolated_workers": 2,
                "pyroxide_max_tasks_per_worker": int(
                    os.environ["PYROXIDE_MAX_TASKS_PER_WORKER"]
                ),
                "pyroxide_recycling_mode": (
                    "disabled"
                    if os.environ["PYROXIDE_MAX_TASKS_PER_WORKER"] == "0"
                    else "enabled"
                ),
                "pyroxide_version": importlib.metadata.version("pyro3"),
                "payload_bytes": [len(payload) for payload in payloads],
                "payload_sha256": [
                    hashlib.sha256(payload).hexdigest() for payload in payloads
                ],
            }
        )
        write_result(Path(os.environ["PYROXIDE_ODOO_OUTPUT"]), result)


def _payloads(*, tasks: int, moves_per_payload: int) -> tuple[bytes, ...]:
    payloads = []
    for task_index in range(tasks):
        lines = []
        for move_index in range(moves_per_payload):
            move_id = task_index * moves_per_payload + move_index + 1
            amount = 10_000 + ((task_index * 101 + move_index * 17) % 80_000)
            base = {
                "account_id": 100 + task_index,
                "company_currency_id": 1,
                "company_id": 1,
                "currency_id": 1,
                "move_id": move_id,
            }
            lines.extend(
                (
                    {
                        **base,
                        "amount_currency_minor": amount,
                        "balance_minor": amount,
                        "credit_minor": 0,
                        "debit_minor": amount,
                        "line_id": move_id * 2,
                    },
                    {
                        **base,
                        "amount_currency_minor": -amount,
                        "balance_minor": -amount,
                        "credit_minor": amount,
                        "debit_minor": 0,
                        "line_id": move_id * 2 + 1,
                    },
                )
            )
        payloads.append(encode_ledger_payload(lines))
    return tuple(payloads)
