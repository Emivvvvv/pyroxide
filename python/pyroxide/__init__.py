"""
Pyroxide: A bounded background task engine for Python powered by Rust.

Exposes a thread-safe background execution engine for offloading operations from
the main Python interpreter. Supports Python callable tasks, sandboxed WebAssembly
execution, and dynamically compiled shared library (dylib) plugins.

Exports:
    - task: Decorator to submit Python functions to the background execution pool.
    - TaskHandle: Object returned by task submission to query status and await results.
    - register_wasm / wasm_task: WebAssembly sandbox registration and execution.
    - compile_rust / dylib_task: Dynamic shared library compilation and execution.
"""

from . import config
from ._pyroxide import (  # noqa: F401
    ForkSafetyError,
    get_status,
    register_dylib,
    submit_task,
)
from ._pyroxide import (
    shutdown_engine as _shutdown_engine,
)
from .config import is_free_threaded, scoped, set_queue_timeout, set_wasm_limits, stats
from .decorators import task
from .plugins import (
    CompilerNotFoundError,
    compile_c,
    compile_rust,
    compile_zig,
    dylib_task,
    load_dylib,
    unregister_dylib,
)
from .stubs import generate_stubs
from .types import TaskHandle
from .wasm import (
    compile_c_wasm,
    compile_rust_wasm,
    compile_wasm,
    compile_wat_wasm,
    compile_zig_wasm,
    load_wasm,
    register_wasm,
    register_wasm_wat,
    wasm_task,
)
from .workflows import TaskGroup, group

__version__ = "0.10.0"


def shutdown(wait: bool = True, cancel_pending: bool = False) -> None:
    """Stop accepting work and shut down Pyroxide's workers.

    Shutdown is irreversible for the current process. Accepted work drains by
    default; ``cancel_pending=True`` cancels work that has not started.
    A Pyroxide worker task must use ``wait=False`` to avoid waiting for itself.
    """
    if type(wait) is not bool or type(cancel_pending) is not bool:
        raise TypeError("wait and cancel_pending must be bool values")
    _shutdown_engine(wait=wait, cancel_pending=cancel_pending)
    if wait:
        from .types import _cleanup_waker

        _cleanup_waker()


__all__ = [
    "task",
    "TaskHandle",
    "register_wasm",
    "register_wasm_wat",
    "wasm_task",
    "load_wasm",
    "compile_wasm",
    "compile_wat_wasm",
    "compile_c_wasm",
    "compile_rust_wasm",
    "compile_zig_wasm",
    "compile_rust",
    "dylib_task",
    "load_dylib",
    "unregister_dylib",
    "compile_c",
    "compile_zig",
    "group",
    "TaskGroup",
    "shutdown",
    "ForkSafetyError",
    "generate_stubs",
    "set_wasm_limits",
    "set_queue_timeout",
    "scoped",
    "is_free_threaded",
    "stats",
    "config",
    "CompilerNotFoundError",
]
