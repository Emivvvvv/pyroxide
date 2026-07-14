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
from .wasm import register_wasm, register_wasm_wat, wasm_task, load_wasm
from .plugins import compile_dylib, dylib_task, compile_c, compile_zig, load_dylib
from .workflows import group, TaskGroup
from .stubs import generate_stubs
from . import config

__version__ = "0.7.0"

__all__ = [
    "task",
    "TaskHandle",
    "register_wasm",
    "register_wasm_wat",
    "wasm_task",
    "load_wasm",
    "compile_dylib",
    "dylib_task",
    "load_dylib",
    "compile_c",
    "compile_zig",
    "group",
    "TaskGroup",
    "generate_stubs",
    "config",
]
