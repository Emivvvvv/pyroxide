"""Public facade for WebAssembly registration, compilation, and tasks."""

from ._wasm_compile import (
    compile_c_wasm,
    compile_rust_wasm,
    compile_wasm,
    compile_wat_wasm,
    compile_zig_wasm,
)
from ._wasm_proxy import (
    WasmProxy,
    load_wasm,
    register_wasm,
    register_wasm_wat,
    wasm_task,
)

__all__ = [
    "compile_c_wasm",
    "compile_rust_wasm",
    "compile_wasm",
    "compile_wat_wasm",
    "compile_zig_wasm",
    "load_wasm",
    "register_wasm",
    "register_wasm_wat",
    "wasm_task",
]

_PUBLIC_OBJECTS = (
    WasmProxy,
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
for _public_object in _PUBLIC_OBJECTS:
    _public_object.__module__ = __name__
del _public_object
