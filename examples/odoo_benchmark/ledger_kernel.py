"""Import-stable, Odoo-free ledger kernel shared by process workers."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Mapping

SCHEMA_VERSION = 1
LINE_FIELDS = frozenset(
    {
        "account_id",
        "amount_currency_minor",
        "balance_minor",
        "company_currency_id",
        "company_id",
        "credit_minor",
        "currency_id",
        "debit_minor",
        "line_id",
        "move_id",
    }
)


def compute_ledger_audit(payload: bytes) -> bytes:
    """Aggregate canonical ledger bytes without importing Odoo or touching the ORM."""
    payload_object = decode_object(payload)
    if set(payload_object) != {"schema_version", "lines"}:
        raise ValueError("payload must contain schema_version and lines only")
    if payload_object["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    raw_lines = payload_object["lines"]
    if not isinstance(raw_lines, list):
        raise ValueError("lines must be a list")
    lines = [validate_line(line) for line in raw_lines]

    totals: dict[tuple[int, int, int], dict[str, int]] = {}
    move_balances: dict[tuple[int, int], int] = defaultdict(int)
    for line in lines:
        group_key = (line["company_id"], line["company_currency_id"], line["currency_id"])
        total = totals.setdefault(
            group_key,
            {
                "company_id": line["company_id"],
                "company_currency_id": line["company_currency_id"],
                "currency_id": line["currency_id"],
                "debit_minor": 0,
                "credit_minor": 0,
                "balance_minor": 0,
                "amount_currency_minor": 0,
            },
        )
        for field in (
            "debit_minor",
            "credit_minor",
            "balance_minor",
            "amount_currency_minor",
        ):
            total[field] += line[field]
        move_balances[(line["company_id"], line["move_id"])] += line["balance_minor"]

    anomalies = [
        {"company_id": company_id, "move_id": move_id, "balance_minor": balance_minor}
        for (company_id, move_id), balance_minor in sorted(move_balances.items())
        if balance_minor != 0
    ]
    return encode_object(
        {
            "schema_version": SCHEMA_VERSION,
            "totals": [totals[key] for key in sorted(totals)],
            "anomalies": anomalies,
        }
    )


def validate_line(value: object) -> dict[str, int]:
    """Validate one primitive ledger line."""
    if not isinstance(value, dict) or set(value) != LINE_FIELDS:
        raise ValueError("ledger lines must contain the exact primitive field set")
    validated: dict[str, int] = {}
    for field in LINE_FIELDS:
        field_value = value[field]
        if isinstance(field_value, bool) or not isinstance(field_value, int):
            raise ValueError("ledger line values must be integers")
        if field.endswith("_id") and field_value <= 0:
            raise ValueError("ledger identifiers must be positive")
        validated[field] = field_value
    if validated["balance_minor"] != validated["debit_minor"] - validated["credit_minor"]:
        raise ValueError("balance_minor must equal debit_minor minus credit_minor")
    return validated


def decode_object(value: bytes) -> dict[str, Any]:
    """Decode a byte-only JSON object."""
    if not isinstance(value, bytes):
        raise ValueError("worker inputs and results must be bytes")
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("invalid JSON bytes") from error
    if not isinstance(decoded, dict):
        raise ValueError("JSON payload must be an object")
    return decoded


def encode_object(value: Mapping[str, object]) -> bytes:
    """Encode a deterministic byte-only JSON object."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
