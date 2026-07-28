import math
import os
import sys
import threading
from contextlib import contextmanager
from typing import Optional

from ._pyroxide import (
    set_global_queue_timeout_ms,
    set_global_wasm_memory_limit_bytes,
    set_global_wasm_timeout_ms,
)

__all__ = [
    "is_free_threaded",
    "scoped",
    "set_queue_timeout",
    "set_wasm_limits",
    "stats",
]

# Thread-local storage for overrides
_local = threading.local()
_MAX_WASM_MEMORY_BYTES = 2**31 - 1


def _positive_int(value: int, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: int, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _wasm_memory_bytes(value: int, name: str) -> int:
    value = _positive_int(value, name)
    if value > _MAX_WASM_MEMORY_BYTES:
        raise ValueError(f"{name} must not exceed {_MAX_WASM_MEMORY_BYTES}")
    return value


def _validate_environment() -> None:
    minimums = {
        "PYROXIDE_WORKERS": 1,
        "PYROXIDE_QUEUE_CAPACITY": 1,
        "PYROXIDE_MAX_PROCESSES": 1,
        "PYROXIDE_SHM_THRESHOLD": 1,
        "PYROXIDE_WASM_TICK_MS": 1,
        "PYROXIDE_WASM_MEMORY_LIMIT_BYTES": 1,
        "PYROXIDE_WASM_TIMEOUT_MS": 1,
        "PYROXIDE_QUEUE_TIMEOUT_MS": 0,
        "PYROXIDE_MAX_TASKS_PER_WORKER": 0,
        "PYROXIDE_WORKER_STARTUP_TIMEOUT_SEC": 1,
        "PYROXIDE_IDLE_TIMEOUT_SEC": 0,
        "PYROXIDE_MIN_WORKERS": 0,
        "PYROXIDE_MAX_IPC_FRAME_BYTES": 1,
        "PYROXIDE_MAX_NATIVE_OUTPUT_BYTES": 1,
    }
    parsed = {}
    for name, minimum in minimums.items():
        raw = os.environ.get(name)
        if raw is None:
            continue
        try:
            value = int(raw)
        except ValueError as error:
            raise ValueError(f"{name} must be an integer") from error
        if value < minimum:
            raise ValueError(f"{name} must be at least {minimum}")
        parsed[name] = value

    compiler_timeout = os.environ.get("PYROXIDE_COMPILER_TIMEOUT_SEC")
    if compiler_timeout is not None:
        try:
            parsed_timeout = float(compiler_timeout)
        except ValueError as error:
            raise ValueError(
                "PYROXIDE_COMPILER_TIMEOUT_SEC must be a positive number"
            ) from error
        if not math.isfinite(parsed_timeout) or parsed_timeout <= 0:
            raise ValueError("PYROXIDE_COMPILER_TIMEOUT_SEC must be a positive number")

    memory_limit = parsed.get("PYROXIDE_WASM_MEMORY_LIMIT_BYTES")
    if memory_limit is not None and memory_limit > _MAX_WASM_MEMORY_BYTES:
        raise ValueError(
            f"PYROXIDE_WASM_MEMORY_LIMIT_BYTES must not exceed {_MAX_WASM_MEMORY_BYTES}"
        )

    default_max_processes = min(os.cpu_count() or 4, 8)
    max_processes = parsed.get("PYROXIDE_MAX_PROCESSES", default_max_processes)
    min_workers = parsed.get("PYROXIDE_MIN_WORKERS", 0)
    if min_workers > max_processes:
        raise ValueError("PYROXIDE_MIN_WORKERS cannot exceed PYROXIDE_MAX_PROCESSES")


_validate_environment()


def is_free_threaded() -> bool:
    """
    Returns True if running under a free-threaded CPython build (PEP 703, Python 3.13+)
    with the Global Interpreter Lock (GIL) disabled.
    """
    if hasattr(sys, "_is_gil_enabled"):
        try:
            return not sys._is_gil_enabled()
        except Exception:
            return False
    return False


def stats() -> dict:
    """
    Return process-local engine gauges and lifetime task counters.

    Gauges include worker count, queue capacity, queued, running, and retained
    active tasks. Counters include submitted, rejected, completed, failed, and
    cancelled tasks.
    """
    from ._pyroxide import get_engine_stats

    return get_engine_stats()


def set_wasm_limits(
    memory_limit_bytes: Optional[int] = None,
    timeout_ms: Optional[int] = None,
):
    """Sets global WebAssembly sandbox execution limits."""
    if memory_limit_bytes is not None:
        set_global_wasm_memory_limit_bytes(
            _wasm_memory_bytes(memory_limit_bytes, "memory_limit_bytes")
        )
    if timeout_ms is not None:
        set_global_wasm_timeout_ms(_positive_int(timeout_ms, "timeout_ms"))


def set_queue_timeout(timeout_ms: int):
    """Sets the global task submission queue timeout in milliseconds."""
    set_global_queue_timeout_ms(_nonnegative_int(timeout_ms, "timeout_ms"))


@contextmanager
def scoped(
    wasm_timeout_ms: Optional[int] = None,
    wasm_memory_limit_bytes: Optional[int] = None,
    queue_timeout_ms: Optional[int] = None,
):
    """
    Context manager to temporarily override execution limits or queue timeouts
    for the current thread.
    """
    # Save current overrides
    prev_wasm_timeout = getattr(_local, "wasm_timeout_ms", None)
    prev_wasm_mem = getattr(_local, "wasm_memory_limit_bytes", None)
    prev_queue_timeout = getattr(_local, "queue_timeout_ms", None)

    # Set new overrides if specified (otherwise inherit current)
    if wasm_timeout_ms is not None:
        _local.wasm_timeout_ms = _positive_int(wasm_timeout_ms, "wasm_timeout_ms")
    if wasm_memory_limit_bytes is not None:
        _local.wasm_memory_limit_bytes = _wasm_memory_bytes(
            wasm_memory_limit_bytes, "wasm_memory_limit_bytes"
        )
    if queue_timeout_ms is not None:
        _local.queue_timeout_ms = _nonnegative_int(queue_timeout_ms, "queue_timeout_ms")

    try:
        yield
    finally:
        # Restore previous overrides
        if prev_wasm_timeout is not None:
            _local.wasm_timeout_ms = prev_wasm_timeout
        elif hasattr(_local, "wasm_timeout_ms"):
            delattr(_local, "wasm_timeout_ms")

        if prev_wasm_mem is not None:
            _local.wasm_memory_limit_bytes = prev_wasm_mem
        elif hasattr(_local, "wasm_memory_limit_bytes"):
            delattr(_local, "wasm_memory_limit_bytes")

        if prev_queue_timeout is not None:
            _local.queue_timeout_ms = prev_queue_timeout
        elif hasattr(_local, "queue_timeout_ms"):
            delattr(_local, "queue_timeout_ms")
