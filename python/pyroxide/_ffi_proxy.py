"""Native FFI proxy implementation."""

from typing import Any, Optional

from ._ffi_types import build_argument_format, build_return_format, validate_ffi_type
from ._pyroxide import submit_dylib_batch, submit_dylib_task
from .types import TaskHandle


class DylibProxy:
    """A proxy representing a dynamically loaded shared library."""

    def __init__(
        self, lib_name: str, signatures: Optional[dict] = None, isolated: bool = False
    ):
        self._lib_name = lib_name
        self._signatures = signatures or {}
        self._isolated = isolated

    def __getattr__(self, symbol_name: str):
        sig = self._signatures.get(symbol_name)
        if sig:
            import struct

            # FFI custom signature call
            args_types = sig.get("args", [])
            if "ret" not in sig:
                raise ValueError(f"FFI signature for '{symbol_name}' must declare 'ret'.")
            ret_type = sig["ret"]

            if len(args_types) > 8:
                raise ValueError(
                    f"FFI signatures support at most 8 arguments; received {len(args_types)}."
                )

            try:
                for t in args_types:
                    validate_ffi_type(t)
                validate_ffi_type(ret_type)
            except ValueError as e:
                raise ValueError(
                    f"Unsupported FFI type for symbol '{symbol_name}': {e}"
                ) from e

            pack_format = build_argument_format(args_types)
            unpack_format = build_return_format(ret_type)
            expected_ret_len = struct.calcsize(unpack_format)

            def ffi_handle(task_id: int) -> TaskHandle:
                handle = TaskHandle(task_id)
                original_result = handle.result

                def ffi_result(
                    timeout_sec: Optional[float] = None, consume: bool = True
                ) -> Any:
                    res_bytes = original_result(
                        timeout_sec=timeout_sec, consume=consume
                    )
                    if len(res_bytes) != expected_ret_len:
                        raise RuntimeError(
                            f"FFI symbol '{symbol_name}' returned {len(res_bytes)} bytes, but {ret_type} requires {expected_ret_len} bytes."
                        )
                    return struct.unpack(unpack_format, res_bytes)[0]

                setattr(handle, "result", ffi_result)
                return handle

            def ffi_method(*args) -> TaskHandle:
                from .config import _get_scoped_queue_timeout_ms

                if len(args) != len(args_types):
                    raise ValueError(
                        f"FFI symbol '{symbol_name}' expects {len(args_types)} arguments, received {len(args)}."
                    )

                try:
                    packed_payload = struct.pack(pack_format, *args)
                except struct.error as e:
                    raise ValueError(
                        f"Argument range or type error for FFI symbol '{symbol_name}': {e}"
                    ) from e

                ffi_sig_arg = (args_types, ret_type)

                queue_time = _get_scoped_queue_timeout_ms()
                task_id = submit_dylib_task(
                    self._lib_name,
                    symbol_name,
                    packed_payload,
                    ffi_sig=ffi_sig_arg,
                    isolated=self._isolated,
                    queue_timeout_ms=queue_time,
                )

                return ffi_handle(task_id)

            def ffi_batch(payloads: list) -> list[TaskHandle]:
                from .config import _get_scoped_queue_timeout_ms

                def pack_payload(payload):
                    args: tuple[Any, ...]
                    if not args_types and (payload == () or payload == [] or payload is None):
                        args = ()
                    elif isinstance(payload, tuple):
                        args = payload
                    else:
                        args = (payload,)

                    if len(args) != len(args_types):
                        raise ValueError(
                            f"FFI symbol '{symbol_name}' expects {len(args_types)} arguments, received {len(args)}."
                        )
                    try:
                        return struct.pack(pack_format, *args)
                    except struct.error as e:
                        raise ValueError(
                            f"Argument range or type error for FFI symbol '{symbol_name}': {e}"
                        ) from e

                ffi_sig_arg = (args_types, ret_type)
                queue_time = _get_scoped_queue_timeout_ms()
                task_ids = submit_dylib_batch(
                    self._lib_name,
                    symbol_name,
                    payloads,
                    ffi_sig=ffi_sig_arg,
                    isolated=self._isolated,
                    queue_timeout_ms=queue_time,
                    payload_builder=pack_payload,
                )
                return [ffi_handle(task_id) for task_id in task_ids]

            setattr(ffi_method, "batch", ffi_batch)
            return ffi_method
        else:
            # Regular bytes/string call
            def dylib_method(payload) -> TaskHandle:
                from .config import _get_scoped_queue_timeout_ms

                queue_time = _get_scoped_queue_timeout_ms()
                task_id = submit_dylib_task(
                    self._lib_name,
                    symbol_name,
                    payload,
                    ffi_sig=None,
                    isolated=self._isolated,
                    queue_timeout_ms=queue_time,
                )
                return TaskHandle(task_id)

            def dylib_batch(payloads: list) -> list[TaskHandle]:
                from .config import _get_scoped_queue_timeout_ms

                queue_time = _get_scoped_queue_timeout_ms()
                task_ids = submit_dylib_batch(
                    self._lib_name,
                    symbol_name,
                    payloads,
                    ffi_sig=None,
                    isolated=self._isolated,
                    queue_timeout_ms=queue_time,
                )
                return [TaskHandle(task_id) for task_id in task_ids]

            setattr(dylib_method, "batch", dylib_batch)
            return dylib_method

