"""Direct CFFI binding for the benchmark byte ABI."""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from typing import Callable

GIL_POLICY = (
    "CFFI ABI mode is a distinct direct binding. Loading _cffi_backend can enable the "
    "GIL on free-threaded CPython, so that interpreter state must be recorded per run."
)


def is_available() -> bool:
    return importlib.util.find_spec("cffi") is not None


def unavailable_reason() -> str:
    return "CFFI is not installed; install the benchmark optional dependency group"


def bind(library_path: str | Path) -> Callable[[bytes], bytes]:
    """Load and declare the C ABI once, returning its warmed call boundary."""
    return _bind(str(Path(library_path).resolve()))


@lru_cache(maxsize=None)
def _bind(library_path: str) -> Callable[[bytes], bytes]:
    if not is_available():
        raise RuntimeError(unavailable_reason())
    from cffi import FFI

    ffi = FFI()
    ffi.cdef(
        """
        unsigned char *benchmark_run(const unsigned char *, size_t, size_t *);
        void benchmark_free(unsigned char *, size_t);
        int benchmark_last_error(void);
        """
    )
    library = ffi.dlopen(library_path)

    def call(payload: bytes) -> bytes:
        output_length = ffi.new("size_t *")
        input_pointer = ffi.from_buffer(payload) if payload else ffi.NULL
        output_pointer = library.benchmark_run(input_pointer, len(payload), output_length)
        if output_pointer == ffi.NULL:
            raise RuntimeError(
                f"native ABI failed with error code {library.benchmark_last_error()}"
            )
        try:
            return bytes(ffi.buffer(output_pointer, output_length[0]))
        finally:
            library.benchmark_free(output_pointer, output_length[0])

    return call


def run(library_path: str | Path, payload: bytes) -> bytes:
    """Call the warmed C ABI and free the exact owned buffer."""
    return bind(library_path)(payload)
