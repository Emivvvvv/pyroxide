"""Odoo-thread ledger extraction and audited result persistence."""

from __future__ import annotations

import base64
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

from ..services.workloads import (
    compute_with_pyroxide,
    decode_audit_result,
    encode_ledger_payload,
)


def extract_ledger_payload(env, domain: Iterable[tuple]) -> bytes:
    """Read posted account lines on the Odoo thread and return primitive bytes only."""
    allowed_company_ids = set(env.companies.ids)
    lines = env["account.move.line"].search(
        list(domain)
        + [
            ("company_id", "in", sorted(allowed_company_ids)),
            ("move_id.state", "=", "posted"),
        ],
        order="company_id, move_id, id",
    )
    facts = []
    for line in lines:
        if line.company_id.id not in allowed_company_ids:
            raise AccessError(_("Ledger line is outside the active company scope."))
        company_currency = line.company_currency_id
        transaction_currency = line.currency_id or company_currency
        debit_minor = _minor_units(line.debit, company_currency)
        credit_minor = _minor_units(line.credit, company_currency)
        facts.append(
            {
                "account_id": line.account_id.id,
                "amount_currency_minor": _minor_units(
                    line.amount_currency, transaction_currency
                ),
                "balance_minor": debit_minor - credit_minor,
                "company_currency_id": company_currency.id,
                "company_id": line.company_id.id,
                "credit_minor": credit_minor,
                "currency_id": transaction_currency.id,
                "debit_minor": debit_minor,
                "line_id": line.id,
                "move_id": line.move_id.id,
            }
        )
    return encode_ledger_payload(facts)


def apply_audit_result(env, result: bytes):
    """Persist one completed audit result on the Odoo request thread."""
    decoded = decode_audit_result(result)
    return env["pyroxide.audit.run"].create(
        {
            "anomaly_count": len(decoded["anomalies"]),
            "result_payload": base64.b64encode(result),
            "state": "done",
        }
    )


class PyroxideAuditRun(models.Model):
    _name = "pyroxide.audit.run"
    _description = "Pyroxide Ledger Audit Run"
    _order = "id desc"

    name = fields.Char(default="Ledger audit", required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    state = fields.Selection(
        [("draft", "Draft"), ("done", "Done")],
        required=True,
        default="draft",
        readonly=True,
    )
    anomaly_count = fields.Integer(readonly=True)
    result_payload = fields.Binary(readonly=True)

    def action_run_current_ledger(self):
        """Perform ORM work on this thread; the isolated adapter receives bytes only."""
        self.ensure_one()
        payload = extract_ledger_payload(self.env, [("company_id", "=", self.company_id.id)])
        try:
            result = compute_with_pyroxide(payload)
        except Exception:
            # Do not write a failed state before re-raising: this transaction rolls back.
            raise
        completed = apply_audit_result(self.env, result)
        return {"type": "ir.actions.act_window", "res_model": completed._name, "res_id": completed.id, "view_mode": "form"}


def _minor_units(value, currency) -> int:
    decimal_places = currency.decimal_places
    scaled = Decimal(str(value)) * (Decimal(10) ** decimal_places)
    rounded = scaled.to_integral_value(rounding=ROUND_HALF_UP)
    if scaled != rounded:
        raise UserError(_("Ledger amount cannot be represented in the currency minor unit."))
    return int(rounded)
