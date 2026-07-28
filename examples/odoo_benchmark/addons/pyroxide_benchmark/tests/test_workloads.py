"""Odoo-independent tests for the byte-only ledger audit kernel."""

from __future__ import annotations

import json

import pytest

from examples.odoo_benchmark.addons.pyroxide_benchmark.services.workloads import (
    compute_ledger_audit,
    encode_ledger_payload,
)


def test_ledger_kernel_uses_integer_minor_units_and_stable_group_order() -> None:
    """Replacing integer totals or sorting must change this deterministic audit result."""
    payload = encode_ledger_payload(
        [
            {
                "account_id": 30,
                "amount_currency_minor": -125,
                "balance_minor": -125,
                "company_currency_id": 2,
                "company_id": 2,
                "credit_minor": 125,
                "currency_id": 2,
                "debit_minor": 0,
                "line_id": 9,
                "move_id": 8,
            },
            {
                "account_id": 10,
                "amount_currency_minor": 125,
                "balance_minor": 125,
                "company_currency_id": 1,
                "company_id": 1,
                "credit_minor": 0,
                "currency_id": 1,
                "debit_minor": 125,
                "line_id": 3,
                "move_id": 4,
            },
            {
                "account_id": 11,
                "amount_currency_minor": -100,
                "balance_minor": -100,
                "company_currency_id": 1,
                "company_id": 1,
                "credit_minor": 100,
                "currency_id": 1,
                "debit_minor": 0,
                "line_id": 4,
                "move_id": 4,
            },
        ]
    )

    result = json.loads(compute_ledger_audit(payload))

    assert result == {
        "anomalies": [
            {"balance_minor": 25, "company_id": 1, "move_id": 4},
            {"balance_minor": -125, "company_id": 2, "move_id": 8},
        ],
        "schema_version": 1,
        "totals": [
            {
                "amount_currency_minor": 25,
                "balance_minor": 25,
                "company_currency_id": 1,
                "company_id": 1,
                "credit_minor": 100,
                "currency_id": 1,
                "debit_minor": 125,
            },
            {
                "amount_currency_minor": -125,
                "balance_minor": -125,
                "company_currency_id": 2,
                "company_id": 2,
                "credit_minor": 125,
                "currency_id": 2,
                "debit_minor": 0,
            },
        ],
    }


def test_kernel_rejects_fractional_or_malformed_ledger_values() -> None:
    """Accepting floats or malformed JSON would let ORM values leak into workers."""
    with pytest.raises(ValueError, match="integers"):
        encode_ledger_payload(
            [
                {
                    "account_id": 1,
                    "amount_currency_minor": 1.5,
                    "balance_minor": 1,
                    "company_currency_id": 1,
                    "company_id": 1,
                    "credit_minor": 0,
                    "currency_id": 1,
                    "debit_minor": 1,
                    "line_id": 1,
                    "move_id": 1,
                }
            ]
        )

    with pytest.raises(ValueError, match="schema_version"):
        compute_ledger_audit(b'{"schema_version": 2, "lines": []}')
