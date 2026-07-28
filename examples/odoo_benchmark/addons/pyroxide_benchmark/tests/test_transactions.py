"""Odoo lifecycle tests; execute only in the provisioned Odoo matrix."""

from __future__ import annotations

import importlib.util
from unittest.mock import patch

if importlib.util.find_spec("odoo") is None:  # pragma: no cover - host pytest only.
    import pytest

    pytest.skip(
        "Odoo TransactionCase tests require the pinned Odoo image",
        allow_module_level=True,
    )

from odoo.tests.common import TransactionCase, tagged

from ..models import audit_run
from ..services import workloads


@tagged("post_install", "-at_install", "pyroxide_benchmark")
class TestLedgerAuditTransactions(TransactionCase):
    def test_adapter_exception_rolls_back_without_a_durable_failed_state(self):
        audit = self.env["pyroxide.audit.run"].create({"name": "rollback check"})
        with patch.object(audit_run, "compute_with_pyroxide", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                audit.action_run_current_ledger()

        self.assertFalse(self.env["pyroxide.audit.run"].search([("state", "=", "failed")]))

    def test_isolated_crash_is_contained_and_the_next_request_can_use_the_kernel(self):
        payload = workloads.encode_ledger_payload([])
        with patch.object(workloads, "compute_with_pyroxide", side_effect=RuntimeError("worker crashed")):
            with self.assertRaisesRegex(RuntimeError, "worker crashed"):
                workloads.compute_with_pyroxide(payload)

        self.assertEqual(workloads.compute_ledger_audit(payload), b'{"anomalies":[],"schema_version":1,"totals":[]}')

    def test_audit_runs_store_results_not_request_local_task_handles(self):
        field_names = self.env["pyroxide.audit.run"]._fields

        self.assertNotIn("task_id", field_names)
        self.assertNotIn("task_handle", field_names)
