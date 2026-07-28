"""Direct ctypes binding for the benchmark byte ABI."""

from __future__ import annotations

import ctypes
import hashlib
from functools import lru_cache
from pathlib import Path

ERROR_NULL_OUTPUT_LENGTH = 1
ERROR_NULL_INPUT = 2
GIL_POLICY = "ctypes.CDLL releases the GIL while each native ABI call is active."


class NativeAbiError(RuntimeError):
    """The native core rejected an ABI call before producing an owned buffer."""

    def __init__(self, error_code: int) -> None:
        super().__init__(f"native ABI failed with error code {error_code}")
        self.error_code = error_code


@lru_cache(maxsize=None)
def _load(library_path: str) -> ctypes.CDLL:
    library = ctypes.CDLL(library_path)
    pointer = ctypes.POINTER(ctypes.c_ubyte)
    library.benchmark_run.argtypes = [pointer, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    library.benchmark_run.restype = pointer
    library.pyroxide_plugin_run.argtypes = [
        pointer,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    library.pyroxide_plugin_run.restype = pointer
    library.benchmark_free.argtypes = [pointer, ctypes.c_size_t]
    library.pyroxide_plugin_free.argtypes = [pointer, ctypes.c_size_t]
    library.benchmark_last_error.restype = ctypes.c_int
    return library


def run(library_path: str | Path, payload: bytes) -> bytes:
    """Call the neutral ABI and free the exact owned allocation before returning."""
    return _run(library_path, payload, "benchmark_run", "benchmark_free")


def run_plugin(library_path: str | Path, payload: bytes) -> bytes:
    """Call the Pyroxide-named export without changing byte or ownership semantics."""
    return _run(
        library_path,
        payload,
        "pyroxide_plugin_run",
        "pyroxide_plugin_free",
    )


def artifact_metadata(library_path: str | Path) -> dict[str, str | int]:
    path = Path(library_path)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def _run(
    library_path: str | Path,
    payload: bytes,
    run_name: str,
    free_name: str,
) -> bytes:
    library = _load(str(library_path))
    output_length = ctypes.c_size_t()
    input_buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
    input_pointer = input_buffer if payload else None
    output_pointer = getattr(library, run_name)(
        input_pointer,
        len(payload),
        ctypes.byref(output_length),
    )
    if not output_pointer:
        raise NativeAbiError(int(library.benchmark_last_error()))
    try:
        return ctypes.string_at(output_pointer, output_length.value)
    finally:
        getattr(library, free_name)(output_pointer, output_length.value)
