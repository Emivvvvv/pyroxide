import functools
from ._pyroxide import register_wasm_module, submit_wasm_task
from .types import TaskHandle


def register_wasm(module_name: str, wasm_bytes: bytes):
    """
    Registers a pre-compiled WebAssembly module in the global registry.
    """
    register_wasm_module(module_name, wasm_bytes)


def register_wasm_wat(module_name: str, wat_str: str):
    """
    Registers a WebAssembly module from WAT text format.
    """
    from ._pyroxide import register_wasm_wat as reg_wat

    reg_wat(module_name, wat_str)


def wasm_task(module_name: str, func_name: str = "run", *, isolated: bool = False):
    """
    Decorator to submit string or byte payloads to be processed by a registered WASM module.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(payload) -> TaskHandle:
            from .config import _local
            wasm_mem = getattr(_local, "wasm_memory_limit_bytes", None)
            wasm_time = getattr(_local, "wasm_timeout_ms", None)
            queue_time = getattr(_local, "queue_timeout_ms", None)
            task_id = submit_wasm_task(
                module_name,
                func_name,
                payload,
                isolated=isolated,
                wasm_memory_limit_bytes=wasm_mem,
                wasm_timeout_ms=wasm_time,
                queue_timeout_ms=queue_time,
            )
            return TaskHandle(task_id)

        return wrapper

    return decorator


class WasmProxy:
    """A proxy representing a registered WebAssembly module."""

    def __init__(self, module_name: str, isolated: bool = False):
        self._module_name = module_name
        self._isolated = isolated

    def __getattr__(self, func_name: str):
        def wasm_method(payload) -> TaskHandle:
            from .config import _local
            wasm_mem = getattr(_local, "wasm_memory_limit_bytes", None)
            wasm_time = getattr(_local, "wasm_timeout_ms", None)
            queue_time = getattr(_local, "queue_timeout_ms", None)
            task_id = submit_wasm_task(
                self._module_name,
                func_name,
                payload,
                isolated=self._isolated,
                wasm_memory_limit_bytes=wasm_mem,
                wasm_timeout_ms=wasm_time,
                queue_timeout_ms=queue_time,
            )
            return TaskHandle(task_id)

        def wasm_batch(payloads: list) -> list[TaskHandle]:
            return [wasm_method(p) for p in payloads]

        wasm_method.batch = wasm_batch
        return wasm_method


def load_wasm(
    module_name: str,
    *,
    generate_stubs: bool = False,
    isolated: bool = False,
) -> WasmProxy:
    """
    Loads a registered WebAssembly (WASM) module and returns an object-oriented proxy
    allowing direct invocation of any exported WASM function on the background worker pool.
    """
    proxy = WasmProxy(module_name, isolated=isolated)
    if generate_stubs:
        from pyroxide.stubs import generate_stubs as run_gen

        run_gen(module_name, library_type="wasm")
    return proxy
