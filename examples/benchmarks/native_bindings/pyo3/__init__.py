"""Availability-gated PyO3 adapter for the shared Rust benchmark core."""

from __future__ import annotations

import importlib
import importlib.util

_MODULE_NAME = "benchmark_pyo3"


def is_available() -> bool:
    return importlib.util.find_spec(_MODULE_NAME) is not None


def unavailable_reason() -> str:
    return (
        "PyO3 extension is not built; run `maturin build --release --manifest-path "
        "examples/benchmarks/native_bindings/pyo3/Cargo.toml` in an environment with maturin"
    )


def run(payload: bytes, _native_library: object = None) -> bytes:
    """Run the same Rust core through the optional extension; it is not a scheduler."""
    if not is_available():
        raise RuntimeError(unavailable_reason())
    return bytes(importlib.import_module(_MODULE_NAME).run(payload))
