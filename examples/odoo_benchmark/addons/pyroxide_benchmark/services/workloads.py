"""Byte-only deterministic ledger audit kernel and lazy Pyroxide adapter.

No Odoo Environment, cursor, recordset, or request-local value crosses this
module's worker boundary.  Odoo-facing code serializes primitive ledger facts
before calling the adapter and applies decoded results on its own thread.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Mapping

from examples.odoo_benchmark.ledger_kernel import (
    SCHEMA_VERSION,
    compute_ledger_audit,
    decode_object,
    encode_object,
    validate_line,
)

POOL_ENVIRONMENT = {
    "PYROXIDE_MAX_PROCESSES": "2",
    "PYROXIDE_MIN_WORKERS": "0",
    "PYROXIDE_WORKERS": "2",
}
_adapter_pid: int | None = None
_isolated_audit: Any | None = None


def encode_ledger_payload(lines: Iterable[Mapping[str, object]]) -> bytes:
    """Canonicalize primitive integer-minor-unit ledger lines into worker bytes."""
    canonical_lines = [validate_line(dict(line)) for line in lines]
    canonical_lines.sort(key=lambda line: (line["company_id"], line["move_id"], line["line_id"]))
    return encode_object({"schema_version": SCHEMA_VERSION, "lines": canonical_lines})


def decode_audit_result(result: bytes) -> dict[str, object]:
    """Decode result bytes before the Odoo request thread persists an audit run."""
    decoded = decode_object(result)
    if set(decoded) != {"schema_version", "totals", "anomalies"}:
        raise ValueError("audit result has an unexpected shape")
    if decoded["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported audit result schema_version")
    if not isinstance(decoded["totals"], list) or not isinstance(decoded["anomalies"], list):
        raise ValueError("audit result collections must be lists")
    return decoded


def initialize_pyroxide_after_fork() -> None:
    """Create the small process-local adapter lazily in an Odoo worker process."""
    global _adapter_pid, _isolated_audit
    process_id = os.getpid()
    if _adapter_pid == process_id and _isolated_audit is not None:
        return
    for name, value in POOL_ENVIRONMENT.items():
        existing = os.environ.get(name)
        if existing is not None and existing != value:
            raise RuntimeError(f"{name} must be {value} for the ledger audit pool")
        os.environ[name] = value
    from pyroxide import task

    _isolated_audit = task(compute_ledger_audit, isolated=True)
    _adapter_pid = process_id


def compute_with_pyroxide(payload: bytes) -> bytes:
    """Run only bytes in the isolated adapter after request-time initialization."""
    initialize_pyroxide_after_fork()
    if _isolated_audit is None:
        raise RuntimeError("isolated ledger audit adapter was not initialized")
    return _isolated_audit(payload).result()


def compute_batch_with_pyroxide(payloads: Iterable[bytes]) -> tuple[bytes, ...]:
    """Submit a complete independent ledger batch before awaiting its results."""
    initialize_pyroxide_after_fork()
    if _isolated_audit is None:
        raise RuntimeError("isolated ledger audit adapter was not initialized")
    handles = tuple(_isolated_audit.batch(list(payloads)))
    return tuple(handle.result() for handle in handles)


def _clear_adapter_after_fork() -> None:
    global _adapter_pid, _isolated_audit
    _adapter_pid = None
    _isolated_audit = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_clear_adapter_after_fork)
