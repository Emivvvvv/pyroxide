import threading
from contextlib import contextmanager
from typing import Optional
from ._pyroxide import (
    set_global_wasm_memory_limit_bytes,
    set_global_wasm_timeout_ms,
    set_global_queue_timeout_ms,
)

# Thread-local storage for overrides
_local = threading.local()


def set_wasm_limits(
    memory_limit_bytes: Optional[int] = None,
    timeout_ms: Optional[int] = None,
):
    """Sets global WebAssembly sandbox execution limits."""
    if memory_limit_bytes is not None:
        set_global_wasm_memory_limit_bytes(memory_limit_bytes)
    if timeout_ms is not None:
        set_global_wasm_timeout_ms(timeout_ms)


def set_queue_timeout(timeout_ms: int):
    """Sets the global task submission queue timeout in milliseconds."""
    set_global_queue_timeout_ms(timeout_ms)


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
        _local.wasm_timeout_ms = wasm_timeout_ms
    if wasm_memory_limit_bytes is not None:
        _local.wasm_memory_limit_bytes = wasm_memory_limit_bytes
    if queue_timeout_ms is not None:
        _local.queue_timeout_ms = queue_timeout_ms

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
