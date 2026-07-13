import functools
from ._pyroxide import register_wasm_module, submit_wasm_task
from .types import TaskHandle


def register_wasm(module_name: str, wasm_bytes: bytes):
    """
    Registers a pre-compiled WebAssembly module in the global registry.
    """
    register_wasm_module(module_name, wasm_bytes)


def wasm_task(module_name: str, func_name: str = "run", *, isolated: bool = False):
    """
    Decorator to submit string or byte payloads to be processed by a registered WASM module.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(payload) -> TaskHandle:
            task_id = submit_wasm_task(module_name, func_name, payload, isolated=isolated)
            return TaskHandle(task_id)

        return wrapper

    return decorator
