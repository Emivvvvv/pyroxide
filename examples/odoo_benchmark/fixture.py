"""Deterministic logical Odoo fixture planning; no database connection is opened."""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any


def build_fixture_plan(*, seed: int) -> dict[str, Any]:
    """Describe identical accounting and attachment fixture content for every profile."""
    generator = random.Random(seed)
    entries = []
    for company_id in (1, 2):
        for move_index in range(3):
            amount_minor = generator.randrange(10_000, 90_000)
            move_id = company_id * 100 + move_index
            entries.extend(
                (
                    {
                        "account_code": "101000",
                        "amount_minor": amount_minor,
                        "company_id": company_id,
                        "credit_minor": 0,
                        "debit_minor": amount_minor,
                        "move_id": move_id,
                    },
                    {
                        "account_code": "400000",
                        "amount_minor": -amount_minor,
                        "company_id": company_id,
                        "credit_minor": amount_minor,
                        "debit_minor": 0,
                        "move_id": move_id,
                    },
                )
            )
    attachments = [
        {
            "name": f"fixture-{index}.bin",
            "payload_sha256": hashlib.sha256(
                f"odoo-fixture:{seed}:{index}".encode("ascii")
            ).hexdigest(),
            "size_bytes": 1024 * (index + 1),
        }
        for index in range(3)
    ]
    logical_content = {"attachments": attachments, "entries": entries, "seed": seed}
    return {
        "attachments": attachments,
        "entries": entries,
        "logical_digest": _digest(logical_content),
        "row_counts": {
            "account_move": 6,
            "account_move_line": 12,
            "ir_attachment": 3,
        },
        "seed": seed,
    }


def _digest(value: object) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()
