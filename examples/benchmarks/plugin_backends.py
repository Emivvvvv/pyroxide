"""Semantic labels for native boundary benchmark cells."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class NativeBackendLabel:
    id: str
    execution_model: str


def native_backend_labels() -> tuple[NativeBackendLabel, ...]:
    """Return non-overlapping labels; bindings are boundaries, not schedulers."""
    return (
        NativeBackendLabel("native_direct_call", "direct native boundary call"),
        NativeBackendLabel(
            "native_binding_thread_pool",
            "direct binding dispatched through a thread pool",
        ),
        NativeBackendLabel("pyroxide_plugin_scheduling", "Pyroxide plugin scheduling"),
    )


class WasmSizeLimitError(ValueError):
    """A payload or guest result exceeds the shared WASM boundary limit."""


DEFAULT_WASM_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class WasmtimeHostSettings:
    pyroxide_wasmtime_version: str
    wasmtime_python_version: str | None
    epoch_interruption: bool
    host_imports: str
    differences: tuple[str, ...]


def wasm_backend_labels() -> tuple[NativeBackendLabel, ...]:
    """Keep guest compilation, instantiation, calls, and Pyroxide scheduling separate."""
    return (
        NativeBackendLabel(
            "wasm_cold_compile_instantiate",
            "WASM module compilation and store instantiation",
        ),
        NativeBackendLabel("wasm_warm_call", "reused WASM instance guest call"),
        NativeBackendLabel("pyroxide_wasm_scheduling", "Pyroxide WASM task scheduling"),
    )


def wasmtime_available() -> bool:
    return importlib.util.find_spec("wasmtime") is not None


def wasmtime_unavailable_reason() -> str:
    return (
        "wasmtime-py is not installed; install a version matching Pyroxide's "
        "locked Wasmtime 36.0.12 before comparing direct-host results"
    )


def pyroxide_wasm_available() -> bool:
    try:
        import pyroxide
    except ModuleNotFoundError:
        return False
    return hasattr(pyroxide, "register_wasm")


def pyroxide_wasm_unavailable_reason() -> str:
    return (
        "Pyroxide's compiled extension is unavailable in this environment; build the local "
        "project with maturin before running the Pyroxide guest probe"
    )


def wasmtime_host_settings() -> WasmtimeHostSettings:
    """Record settings shared with Pyroxide and any direct-host differences."""
    version = None
    if wasmtime_available():
        version = importlib.metadata.version("wasmtime")
    differences = [
        "Pyroxide increments the engine epoch from its configured ticker; direct "
        "correctness calls do not set an execution deadline.",
        "Both hosts instantiate the import-free core module with epoch interruption enabled.",
    ]
    if version is None:
        differences.append("wasmtime-py is unavailable on this host.")
    elif version != "36.0.12":
        differences.append(
            f"wasmtime-py {version} differs from Pyroxide's locked Wasmtime 36.0.12."
        )
    return WasmtimeHostSettings(
        pyroxide_wasmtime_version="36.0.12",
        wasmtime_python_version=version,
        epoch_interruption=True,
        host_imports="none",
        differences=tuple(differences),
    )


def run_pyroxide_wasm(
    wasm_bytes: bytes,
    payload: bytes,
    *,
    function_name: str = "run",
    memory_limit_bytes: int = DEFAULT_WASM_MEMORY_LIMIT_BYTES,
) -> bytes:
    """Run the documented guest ABI through Pyroxide for bounded correctness only."""
    if not pyroxide_wasm_available():
        raise RuntimeError(pyroxide_wasm_unavailable_reason())
    import pyroxide

    module_name = "benchmark_wasm_" + hashlib.sha256(wasm_bytes).hexdigest()[:16]
    pyroxide.register_wasm(module_name, wasm_bytes)

    @pyroxide.wasm_task(module_name, function_name)
    def call_guest(_: bytes):
        raise AssertionError("Pyroxide must route this call to the registered WASM guest")

    with pyroxide.scoped(wasm_memory_limit_bytes=memory_limit_bytes):
        return call_guest(payload).result()


def run_wasmtime_wasm(
    wasm_bytes: bytes,
    payload: bytes,
    *,
    memory_limit_bytes: int = DEFAULT_WASM_MEMORY_LIMIT_BYTES,
) -> bytes:
    """Run the same import-free guest ABI directly through wasmtime-py when installed."""
    if not wasmtime_available():
        raise RuntimeError(wasmtime_unavailable_reason())
    _validate_wasm_input(payload, memory_limit_bytes)
    import wasmtime

    config = wasmtime.Config()
    config.epoch_interruption = True
    engine = wasmtime.Engine(config)
    module = wasmtime.Module(engine, wasm_bytes)
    store = wasmtime.Store(engine)
    store.set_epoch_deadline(1_000_000_000)
    store.set_limits(memory_size=memory_limit_bytes)
    instance = wasmtime.Instance(store, module, [])
    exports = instance.exports(store)
    memory = exports["memory"]
    alloc = exports["alloc"]
    dealloc = exports["dealloc"]
    run = exports["run"]
    input_pointer = alloc(store, len(payload))
    output_pointer = 0
    output_length = 0
    try:
        memory.write(store, payload, input_pointer)
        packed = run(store, input_pointer, len(payload))
        output_pointer = packed >> 32
        output_length = packed & 0xFFFF_FFFF
        _validate_wasm_output(memory, store, output_pointer, output_length, memory_limit_bytes)
        return bytes(memory.read(store, output_pointer, output_pointer + output_length))
    finally:
        dealloc(store, input_pointer, len(payload))
        if output_pointer and output_length:
            dealloc(store, output_pointer, output_length)


def wasm_artifact_metadata(wasm_path: str | Path) -> dict[str, str | int]:
    path = Path(wasm_path)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def extism_compatible_with_guest_abi() -> bool:
    """Extism's PDK function ABI is not this raw linear-memory guest ABI."""
    return False


def extism_exclusion_reason() -> str:
    return (
        "Extism is excluded because its PDK function ABI would require an adapter that "
        "changes the measured guest boundary."
    )


def _validate_wasm_input(payload: bytes, memory_limit_bytes: int) -> None:
    if len(payload) > memory_limit_bytes:
        raise WasmSizeLimitError(
            f"WASM input length {len(payload)} exceeds memory limit {memory_limit_bytes}"
        )


def _validate_wasm_output(
    memory: object,
    store: object,
    pointer: int,
    length: int,
    memory_limit_bytes: int,
) -> None:
    if pointer < 0 or length < 0:
        raise WasmSizeLimitError("WASM returned a negative output range")
    if length > memory_limit_bytes:
        raise WasmSizeLimitError(
            f"WASM output length {length} exceeds memory limit {memory_limit_bytes}"
        )
    if pointer + length > memory.data_len(store):
        raise WasmSizeLimitError("WASM output range exceeds guest memory")
