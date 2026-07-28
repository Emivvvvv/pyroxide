"""Availability-gated nanobind adapter for the neutral C ABI."""

from __future__ import annotations

import importlib
import importlib.util

_MODULE_NAME = "benchmark_nanobind"


def is_available() -> bool:
    return importlib.util.find_spec(_MODULE_NAME) is not None


def unavailable_reason() -> str:
    return (
        "nanobind extension is not built; set NANOBIND_ROOT and BENCHMARK_CORE_LIBRARY, "
        "then configure examples/benchmarks/native_bindings/nanobind with CMake"
    )


def run(payload: bytes, _native_library: object = None) -> bytes:
    """Run the neutral C ABI through nanobind; it is not a task scheduler."""
    if not is_available():
        raise RuntimeError(unavailable_reason())
    return bytes(importlib.import_module(_MODULE_NAME).run(payload))
