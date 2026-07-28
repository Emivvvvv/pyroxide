"""Odoo TransactionCase coverage; execute only in the provisioned Odoo matrix."""

from __future__ import annotations

import importlib.util
import json

if importlib.util.find_spec("odoo") is None:  # pragma: no cover - host pytest only.
    import pytest

    pytest.skip(
        "Odoo TransactionCase tests require the pinned Odoo image",
        allow_module_level=True,
    )

from odoo import Command
from odoo.tests.common import TransactionCase, tagged

from ..models.audit_run import apply_audit_result, extract_ledger_payload
from ..services.workloads import compute_ledger_audit, compute_with_pyroxide


@tagged("post_install", "-at_install", "pyroxide_benchmark")
class TestLedgerAuditCorrectness(TransactionCase):
    def _journal_and_account(self, company):
        journal_model = self.env["account.journal"].with_company(company)
        journal = journal_model.search(
            [("type", "=", "general"), ("company_id", "=", company.id)], limit=1
        )
        if not journal:
            journal = journal_model.create(
                {
                    "name": f"Pyroxide benchmark {company.id}",
                    "code": f"PYR{company.id:02d}",
                    "type": "general",
                    "company_id": company.id,
                }
            )
        account = self.env["account.account"].with_company(company).create(
            {
                "name": f"Pyroxide benchmark {company.id}",
                "code": f"PYR{company.id:02d}",
                "account_type": "asset_current",
                "company_ids": [Command.set([company.id])],
            }
        )
        return journal, account

    def _posted_move(self, company, debit, credit):
        journal, account = self._journal_and_account(company)
        move = self.env["account.move"].with_company(company).create(
            {
                "company_id": company.id,
                "move_type": "entry",
                "journal_id": journal.id,
                "line_ids": [
                    Command.create({"account_id": account.id, "debit": debit, "credit": 0}),
                    Command.create({"account_id": account.id, "debit": 0, "credit": credit}),
                ],
            }
        )
        move.action_post()
        return move

    def test_posted_multi_company_ledger_uses_integer_minor_units_and_stable_order(self):
        second_company = self.env["res.company"].create({"name": "Audit company B"})
        first_move = self._posted_move(self.env.company, 12.50, 12.50)
        second_move = self._posted_move(second_company, 9.00, 9.00)

        scoped_env = self.env["account.move.line"].with_context(
            allowed_company_ids=[self.env.company.id, second_company.id]
        ).env
        payload = extract_ledger_payload(
            scoped_env,
            [("move_id", "in", [second_move.id, first_move.id])],
        )
        result = json.loads(compute_ledger_audit(payload))

        self.assertEqual(result["anomalies"], [])
        self.assertEqual([total["company_id"] for total in result["totals"]], sorted([self.env.company.id, second_company.id]))
        self.assertTrue(all(isinstance(total["debit_minor"], int) for total in result["totals"]))

        self.assertEqual(compute_with_pyroxide(payload), compute_ledger_audit(payload))

    def test_anomalous_payload_is_retained_as_an_audit_anomaly(self):
        payload = b'{"lines":[{"account_id":1,"amount_currency_minor":100,"balance_minor":100,"company_currency_id":1,"company_id":1,"credit_minor":0,"currency_id":1,"debit_minor":100,"line_id":1,"move_id":99}],"schema_version":1}'

        result = compute_ledger_audit(payload)
        audit_run = apply_audit_result(self.env, result)

        self.assertEqual(audit_run.anomaly_count, 1)
        self.assertEqual(audit_run.state, "done")

    def test_cross_company_domain_is_limited_by_active_company_access(self):
        other_company = self.env["res.company"].create({"name": "Outside scope"})
        outside_move = self._posted_move(other_company, 4.00, 4.00)

        scoped_env = self.env["account.move.line"].with_context(
            allowed_company_ids=[self.env.company.id]
        ).env
        payload = extract_ledger_payload(scoped_env, [("move_id", "=", outside_move.id)])

        self.assertEqual(json.loads(payload)["lines"], [])
