"""
Pyroxide: A high-performance, lock-free background task broker for Python powered by Rust.

Exposes a thread-safe background execution engine for offloading operations from
the main Python interpreter. Supports Python callable tasks, sandboxed WebAssembly
execution, and dynamically compiled shared library (dylib) plugins.

Exports:
    - task: Decorator to submit Python functions to the background execution pool.
    - TaskHandle: Object returned by task submission to query status and await results.
    - register_wasm / wasm_task: WebAssembly sandbox registration and execution.
    - compile_dylib / dylib_task: Dynamic shared library compilation and execution.
"""

from ._pyroxide import submit_task, get_status, register_dylib  # noqa: F401
from .decorators import task
from .types import TaskHandle
from .wasm import register_wasm, wasm_task
from .plugins import compile_dylib, dylib_task, compile_c, compile_zig
from .workflows import group, TaskGroup

__version__ = "0.5.2"

__all__ = [
    "task",
    "TaskHandle",
    "register_wasm",
    "wasm_task",
    "compile_dylib",
    "dylib_task",
    "compile_c",
    "compile_zig",
    "group",
    "TaskGroup",
]
