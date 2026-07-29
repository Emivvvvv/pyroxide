"""Native task registration and loading implementation."""

from typing import Any, Callable, Optional

from ._ffi_proxy import DylibProxy
from ._pyroxide import register_dylib, submit_dylib_batch, submit_dylib_task
from .types import TaskHandle


def dylib_task(
    dylib_name: str,
    symbol_name: str = "pyroxide_plugin_run",
    ffi_sig: Optional[tuple] = None,
    *,
    isolated: bool = False,
):
    """
    Decorator that routes task payloads to a registered dynamic shared library (dylib)
    for GIL-free execution on the background Rust worker pool.

    The dylib must have been previously compiled and registered via ``compile_rust()``.

    Args:
        dylib_name: The name of the dylib as registered with ``compile_rust()``.
        symbol_name: The function symbol to load from the dylib. Defaults to "pyroxide_plugin_run".
        ffi_sig: Optional FFI signature tuple, e.g. (['i32', 'i32'], 'i32')
        isolated: Set to True to run in an isolated worker process for crash isolation.
    """

    def decorator(func: Callable[[Any], Any]) -> Callable[[Any], TaskHandle]:
        def wrapper(payload: Any) -> TaskHandle:
            from .config import _get_scoped_queue_timeout_ms

            queue_time = _get_scoped_queue_timeout_ms()
            task_id = submit_dylib_task(
                dylib_name,
                symbol_name,
                payload,
                ffi_sig=ffi_sig,
                isolated=isolated,
                queue_timeout_ms=queue_time,
            )
            return TaskHandle(task_id)

        def batch(payloads: list) -> list[TaskHandle]:
            from .config import _get_scoped_queue_timeout_ms

            queue_time = _get_scoped_queue_timeout_ms()
            task_ids = submit_dylib_batch(
                dylib_name,
                symbol_name,
                payloads,
                ffi_sig=ffi_sig,
                isolated=isolated,
                queue_timeout_ms=queue_time,
            )
            return [TaskHandle(task_id) for task_id in task_ids]

        setattr(wrapper, "batch", batch)
        return wrapper

    return decorator



def load_dylib(
    lib_name: str,
    *,
    signatures: Optional[dict] = None,
    generate_stubs: bool = False,
    isolated: bool = False,
    free_fn_name: Optional[str] = None,
) -> DylibProxy:
    """
    Loads a registered dynamic shared library (dylib) and returns an object-oriented proxy
    allowing direct invocation of any C-ABI exported symbol on the background worker pool.
    """
    # 0. Auto-register if not already registered
    try:
        from pyroxide._pyroxide import get_dylib_exports, get_dylib_path

        get_dylib_exports(lib_name)
        if free_fn_name is not None:
            try:
                reg_path = get_dylib_path(lib_name)
                if reg_path:
                    clean_path = reg_path.split(";")[0]
                    register_dylib(lib_name, clean_path, free_fn_name=free_fn_name)
            except Exception:
                pass
    except ValueError:
        try:
            register_dylib(lib_name, lib_name, free_fn_name=free_fn_name)
        except Exception:
            pass

    # 1. Auto-discover signatures if none are provided
    if signatures is None:
        from pyroxide._pyroxide import get_dylib_metadata

        metadata_str = get_dylib_metadata(lib_name)
        if metadata_str:
            signatures = {}
            for entry in metadata_str.split(";"):
                if not entry:
                    continue
                func_parts = entry.split(":")
                if len(func_parts) == 2:
                    func_name, sig_part = func_parts
                    sig_parts = sig_part.split("|")
                    if len(sig_parts) == 2:
                        args_part, ret_type = sig_parts
                        args = [a for a in args_part.split(",") if a]
                        signatures[func_name] = {"args": args, "ret": ret_type}

    # 2. Create the proxy
    proxy_class_name = f"{lib_name.capitalize()}DylibProxy"
    ProxyClass = type(proxy_class_name, (DylibProxy,), {})
    proxy = ProxyClass(lib_name, signatures=signatures, isolated=isolated)

    # 3. Generate stubs if requested
    if generate_stubs:
        from pyroxide.stubs import generate_stubs as run_gen

        run_gen(lib_name, library_type="dylib")

    return proxy


def unregister_dylib(name: str) -> None:
    """
    Unregisters a dynamic shared library from the Pyroxide registries.
    """
    from pyroxide._pyroxide import unregister_dylib as _raw_unregister

    _raw_unregister(name)
