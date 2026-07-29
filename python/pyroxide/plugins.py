"""Public facade for native compilation and dynamic-library tasks."""

from ._ffi_proxy import DylibProxy
from ._native_compile import (
    CompilerNotFoundError,
    CrossProcessLock,
    compile_c,
    compile_rust,
    compile_zig,
)
from ._native_plugins import (
    dylib_task,
    load_dylib,
    unregister_dylib,
)

__all__ = [
    "CompilerNotFoundError",
    "compile_c",
    "compile_rust",
    "compile_zig",
    "dylib_task",
    "load_dylib",
    "unregister_dylib",
]

_PUBLIC_OBJECTS = (
    CompilerNotFoundError,
    CrossProcessLock,
    DylibProxy,
    compile_c,
    compile_rust,
    compile_zig,
    dylib_task,
    load_dylib,
    unregister_dylib,
)
for _public_object in _PUBLIC_OBJECTS:
    _public_object.__module__ = __name__
del _public_object
