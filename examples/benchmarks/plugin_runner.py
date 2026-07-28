"""Run native/WASM boundary comparisons and save report-schema JSONL.

This controller measures application-visible calls only. Compilation, extension
loading, guest registration, and warm-up happen before each timed block.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import platform
import random
import struct
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10.
    import tomli as tomllib

from . import plugin_backends
from .native_bindings import cffi_adapter, ctypes_adapter, nanobind, pyo3

SCHEMA_VERSION = 1
_FRAME_VERSION = 1
_FRAME_BYTES = 52
_MIX_MULTIPLIER_1 = 0xBF58476D1CE4E5B9
_MIX_MULTIPLIER_2 = 0x94D049BB133111EB
_MIX_SEED = 0x9E3779B97F4A7C15
_MASK_64 = (1 << 64) - 1
_KNOWN_BACKENDS = frozenset(
    {
        "ctypes-direct",
        "cffi-direct",
        "pyo3-direct",
        "nanobind-direct",
        "pyroxide-dylib-scheduled",
        "wasmtime-cold",
        "wasmtime-warm",
        "pyroxide-wasm-scheduled",
    }
)


@dataclass(frozen=True, slots=True)
class PluginExperiment:
    id: str
    workload: str
    semantics: str
    backends: tuple[str, ...]
    iterations: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("experiment id", self.id),
            ("workload", self.workload),
            ("semantics", self.semantics),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not self.backends or len(self.backends) != len(set(self.backends)):
            raise ValueError("experiment backends must be non-empty and unique")
        if self.iterations is not None and (
            isinstance(self.iterations, bool)
            or not isinstance(self.iterations, int)
            or self.iterations <= 0
        ):
            raise ValueError("experiment iterations must be a positive integer")


@dataclass(frozen=True, slots=True)
class PluginProfile:
    blocks: int
    iterations: int
    payload_bytes: int
    random_seed: int
    experiments: tuple[PluginExperiment, ...]

    def __post_init__(self) -> None:
        if isinstance(self.blocks, bool) or not isinstance(self.blocks, int) or self.blocks < 3:
            raise ValueError("blocks must be an integer of at least 3")
        for name, value in (
            ("iterations", self.iterations),
            ("payload_bytes", self.payload_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(self.random_seed, bool) or not isinstance(self.random_seed, int):
            raise ValueError("random_seed must be an integer")
        if not self.experiments:
            raise ValueError("experiments must be non-empty")
        ids = [experiment.id for experiment in self.experiments]
        if len(ids) != len(set(ids)):
            raise ValueError("experiment ids must be unique")
        semantics = [experiment.semantics for experiment in self.experiments]
        if len(semantics) != len(set(semantics)):
            raise ValueError("experiments must not merge distinct boundary layers")

    @classmethod
    def from_toml(cls, path: str | Path) -> PluginProfile:
        with Path(path).open("rb") as profile_file:
            values = tomllib.load(profile_file)
        _require_exact_keys(
            values,
            {"schema_version", "run", "experiments"},
            "profile",
        )
        if values["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported plugin profile schema_version")
        run = _require_table(values["run"], "run")
        _require_exact_keys(
            run,
            {"blocks", "iterations", "payload_bytes", "random_seed"},
            "run",
        )
        experiments = values["experiments"]
        if not isinstance(experiments, list):
            raise ValueError("experiments must be an array")
        parsed = []
        for raw_experiment in experiments:
            experiment = _require_table(raw_experiment, "experiment")
            required = {"id", "workload", "semantics", "backends"}
            if set(experiment) - (required | {"iterations"}) or required - set(
                experiment
            ):
                raise ValueError("experiment has missing or unknown keys")
            backends = experiment["backends"]
            if not isinstance(backends, list) or not all(
                isinstance(backend, str) for backend in backends
            ):
                raise ValueError("experiment backends must be an array of strings")
            unknown = sorted(set(backends) - _KNOWN_BACKENDS)
            if unknown:
                raise ValueError("unknown plugin backends: " + ", ".join(unknown))
            parsed.append(
                PluginExperiment(
                    id=experiment["id"],
                    workload=experiment["workload"],
                    semantics=experiment["semantics"],
                    backends=tuple(backends),
                    iterations=experiment.get("iterations"),
                )
            )
        return cls(
            blocks=run["blocks"],
            iterations=run["iterations"],
            payload_bytes=run["payload_bytes"],
            random_seed=run["random_seed"],
            experiments=tuple(parsed),
        )


@dataclass(frozen=True, slots=True)
class ComparisonCell:
    id: str
    artifact_hash: str
    run: Callable[[bytes], bytes] | None = None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if self.id not in _KNOWN_BACKENDS and not self.id:
            raise ValueError("cell id must be non-empty")
        if (
            len(self.artifact_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.artifact_hash)
        ):
            raise ValueError("cell artifact_hash must be a lowercase SHA-256 digest")
        if (self.run is None) == (self.unavailable_reason is None):
            raise ValueError("cell must be either runnable or explicitly unavailable")


def expected_frame(payload: bytes) -> bytes:
    """Return an independent oracle for the native/WASM shared result frame."""
    state = _MIX_SEED
    for value in payload:
        state ^= value
        state = (state * _MIX_MULTIPLIER_1) & _MASK_64
        state ^= state >> 31
        state = (state * _MIX_MULTIPLIER_2) & _MASK_64
        state ^= state >> 27
    frame = struct.pack("<IQQ", _FRAME_VERSION, len(payload), state)
    result = frame + hashlib.sha256(payload).digest()
    if len(result) != _FRAME_BYTES:  # pragma: no cover - invariant guard.
        raise AssertionError("unexpected benchmark frame size")
    return result


def run_experiment(
    experiment: PluginExperiment,
    cells: Mapping[str, ComparisonCell],
    *,
    output_path: Path,
    blocks: int,
    iterations: int,
    payload: bytes,
    expected: bytes,
    environment: str,
    random_seed: int = 1729,
) -> None:
    """Write one paired block per selected cell without hiding failures."""
    if blocks < 3:
        raise ValueError("at least three blocks are required by the report contract")
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    selected = []
    for backend in experiment.backends:
        try:
            selected.append(cells[backend])
        except KeyError as error:
            raise ValueError(f"missing comparison cell: {backend}") from error

    output_path.parent.mkdir(parents=True, exist_ok=True)
    namespace = uuid.uuid4().hex
    workload_hash = hashlib.sha256(payload).hexdigest()
    randomizer = random.Random(random_seed)
    with output_path.open("w", encoding="utf-8") as output:
        for block_index in range(blocks):
            block_cells = list(selected)
            randomizer.shuffle(block_cells)
            for cell in block_cells:
                record = _measure_cell(
                    experiment,
                    cell,
                    block_index=block_index,
                    iterations=iterations,
                    payload=payload,
                    expected=expected,
                    environment=environment,
                    namespace=namespace,
                    workload_hash=workload_hash,
                )
                output.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
                output.flush()


def build_native_cells(native_library: Path) -> dict[str, ComparisonCell]:
    """Build all native rows, retaining unavailable extensions as explicit cells."""
    source = Path(__file__).resolve()
    library_exists = native_library.is_file()
    library_reason = f"native core artifact does not exist: {native_library}"
    native_hash = _hash_artifacts(
        (source, native_library),
        (f"python={platform.python_version()}",),
    )

    if library_exists:
        ctypes_cell = ComparisonCell(
            id="ctypes-direct",
            artifact_hash=_hash_artifacts(
                (source, Path(ctypes_adapter.__file__), native_library),
                (f"python={platform.python_version()}",),
            ),
            run=lambda payload: ctypes_adapter.run(native_library, payload),
        )
    else:
        ctypes_cell = ComparisonCell(
            id="ctypes-direct",
            artifact_hash=native_hash,
            unavailable_reason=library_reason,
        )

    cffi_cell = _optional_native_cell(
        "cffi-direct",
        cffi_adapter,
        native_library,
        library_reason,
    )
    pyo3_cell = _optional_native_cell(
        "pyo3-direct",
        pyo3,
        native_library,
        library_reason,
        artifact_required=False,
    )
    nanobind_cell = _optional_native_cell(
        "nanobind-direct",
        nanobind,
        native_library,
        library_reason,
        artifact_required=False,
    )
    scheduled = _pyroxide_dylib_cell(native_library, library_reason)
    return {
        cell.id: cell
        for cell in (ctypes_cell, cffi_cell, pyo3_cell, nanobind_cell, scheduled)
    }


def build_wasm_cells(wasm_module: Path) -> dict[str, ComparisonCell]:
    """Build cold, warm, and scheduled WASM rows without substituting hosts."""
    source = Path(__file__).resolve()
    module_exists = wasm_module.is_file()
    module_reason = f"WASM guest artifact does not exist: {wasm_module}"
    backend_hash = _hash_artifacts(
        (source, Path(plugin_backends.__file__), wasm_module),
        ("host=unavailable",),
    )
    if not module_exists:
        return {
            backend: ComparisonCell(
                id=backend,
                artifact_hash=backend_hash,
                unavailable_reason=module_reason,
            )
            for backend in (
                "wasmtime-cold",
                "wasmtime-warm",
                "pyroxide-wasm-scheduled",
            )
        }

    wasm_bytes = wasm_module.read_bytes()
    if plugin_backends.wasmtime_available():
        wasmtime_module = importlib.import_module("wasmtime")
        wasmtime_hash = _hash_artifacts(
            (
                source,
                Path(plugin_backends.__file__),
                wasm_module,
                Path(wasmtime_module.__file__),
            ),
            (_distribution_identity("wasmtime"),),
        )
        cold = ComparisonCell(
            id="wasmtime-cold",
            artifact_hash=wasmtime_hash,
            run=lambda payload: plugin_backends.run_wasmtime_wasm(wasm_bytes, payload),
        )
        warm = ComparisonCell(
            id="wasmtime-warm",
            artifact_hash=wasmtime_hash,
            run=_lazy(_wasmtime_warm_factory(wasm_bytes)),
        )
    else:
        reason = plugin_backends.wasmtime_unavailable_reason()
        cold = ComparisonCell(
            id="wasmtime-cold",
            artifact_hash=backend_hash,
            unavailable_reason=reason,
        )
        warm = ComparisonCell(
            id="wasmtime-warm",
            artifact_hash=backend_hash,
            unavailable_reason=reason,
        )

    if plugin_backends.pyroxide_wasm_available():
        pyroxide_path = _module_path("pyroxide._pyroxide")
        scheduled = ComparisonCell(
            id="pyroxide-wasm-scheduled",
            artifact_hash=_hash_artifacts(
                (
                    source,
                    Path(plugin_backends.__file__),
                    wasm_module,
                    pyroxide_path,
                ),
                (_distribution_identity("pyro3"),),
            ),
            run=_lazy(_pyroxide_wasm_factory(wasm_bytes)),
        )
    else:
        scheduled = ComparisonCell(
            id="pyroxide-wasm-scheduled",
            artifact_hash=backend_hash,
            unavailable_reason=plugin_backends.pyroxide_wasm_unavailable_reason(),
        )
    return {cell.id: cell for cell in (cold, warm, scheduled)}


def run_profile(
    profile: PluginProfile,
    *,
    native_library: Path,
    wasm_module: Path,
    output_directory: Path,
    overwrite: bool = False,
) -> tuple[Path, ...]:
    """Execute every declared semantic experiment and save its availability."""
    output_directory.mkdir(parents=True, exist_ok=True)
    paths = tuple(
        output_directory / f"{experiment.id}.jsonl"
        for experiment in profile.experiments
    )
    availability_path = output_directory / "availability.json"
    existing = [path for path in (*paths, availability_path) if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "refusing to overwrite benchmark evidence: "
            + ", ".join(str(path) for path in existing)
        )

    native_cells = build_native_cells(native_library)
    wasm_cells = build_wasm_cells(wasm_module)
    cells = {**native_cells, **wasm_cells}
    environment = _environment_label()
    availability = {
        "schema_version": SCHEMA_VERSION,
        "environment": environment,
        "backends": {
            backend: {
                "available": cell.run is not None,
                "reason": cell.unavailable_reason,
                "artifact_hash": cell.artifact_hash,
            }
            for backend, cell in sorted(cells.items())
        },
    }
    availability_path.write_text(
        json.dumps(availability, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    payload = random.Random(profile.random_seed).randbytes(profile.payload_bytes)
    expected = expected_frame(payload)
    for experiment, path in zip(profile.experiments, paths, strict=True):
        run_experiment(
            experiment,
            cells,
            output_path=path,
            blocks=profile.blocks,
            iterations=experiment.iterations or profile.iterations,
            payload=payload,
            expected=expected,
            environment=environment,
            random_seed=profile.random_seed,
        )
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_profile = Path(__file__).with_name("manifests") / "plugin-boundaries.toml"
    parser.add_argument("--profile", type=Path, default=default_profile)
    parser.add_argument("--native-library", type=Path, required=True)
    parser.add_argument("--wasm-module", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    profile = PluginProfile.from_toml(args.profile)
    run_profile(
        profile,
        native_library=args.native_library,
        wasm_module=args.wasm_module,
        output_directory=args.output_directory,
        overwrite=args.overwrite,
    )
    return 0


def _measure_cell(
    experiment: PluginExperiment,
    cell: ComparisonCell,
    *,
    block_index: int,
    iterations: int,
    payload: bytes,
    expected: bytes,
    environment: str,
    namespace: str,
    workload_hash: str,
) -> dict[str, object]:
    hashes = {"workload": workload_hash, "backend": cell.artifact_hash}
    common: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": f"{namespace}:{block_index}:{cell.id}",
        "experiment_id": experiment.id,
        "workload": experiment.workload,
        "environment": environment,
        "semantics": experiment.semantics,
        "artifact_hashes": hashes,
        "artifact_checksum": hashlib.sha256(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "backend": cell.id,
        "block_index": block_index,
        "workers": 1,
    }
    if cell.run is None:
        return {**common, "status": "error", "error": cell.unavailable_reason}

    try:
        warm_result = bytes(cell.run(payload))
        if warm_result != expected:
            raise RuntimeError("correctness mismatch during untimed warm-up")
        rss_before = _process_tree_rss_bytes()
        started = time.perf_counter_ns()
        result = b""
        for _ in range(iterations):
            result = bytes(cell.run(payload))
        elapsed_ns = max(time.perf_counter_ns() - started, 1)
        rss_after = _process_tree_rss_bytes()
        if result != expected:
            raise RuntimeError("correctness mismatch after timed calls")
        elapsed_seconds = elapsed_ns / 1_000_000_000
        return {
            **common,
            "status": "ok",
            "latency_seconds": elapsed_seconds / iterations,
            "throughput_tasks_per_second": iterations / elapsed_seconds,
            "peak_process_tree_rss_bytes": max(rss_before, rss_after),
        }
    except Exception as error:
        return {
            **common,
            "status": "error",
            "error": f"{type(error).__name__}: {error}",
        }


def _optional_native_cell(
    cell_id: str,
    adapter: object,
    native_library: Path,
    library_reason: str,
    *,
    artifact_required: bool = True,
) -> ComparisonCell:
    source_paths = [Path(__file__).resolve(), Path(adapter.__file__)]
    if native_library.is_file():
        source_paths.append(native_library)
    identifiers = (
        (_distribution_identity("cffi"),)
        if adapter is cffi_adapter
        else (f"python={platform.python_version()}",)
    )
    artifact_hash = _hash_artifacts(tuple(source_paths), identifiers)
    if artifact_required and not native_library.is_file():
        return ComparisonCell(
            id=cell_id,
            artifact_hash=artifact_hash,
            unavailable_reason=library_reason,
        )
    if not adapter.is_available():
        return ComparisonCell(
            id=cell_id,
            artifact_hash=artifact_hash,
            unavailable_reason=adapter.unavailable_reason(),
        )
    module_name = getattr(adapter, "_MODULE_NAME", None)
    if module_name:
        module = importlib.import_module(module_name)
        module_path = Path(module.__file__)
        artifact_hash = _hash_artifacts(
            (*source_paths, module_path),
            (f"module={module_name}",),
        )
    elif adapter is cffi_adapter:
        return ComparisonCell(
            id=cell_id,
            artifact_hash=artifact_hash,
            run=cffi_adapter.bind(native_library),
        )
    return ComparisonCell(
        id=cell_id,
        artifact_hash=artifact_hash,
        run=lambda payload: adapter.run(payload, native_library)
        if not artifact_required
        else adapter.run(native_library, payload),
    )


def _pyroxide_dylib_cell(
    native_library: Path,
    library_reason: str,
) -> ComparisonCell:
    artifact_hash = _hash_artifacts(
        (
            Path(__file__).resolve(),
            Path(plugin_backends.__file__),
            native_library,
            _module_path("pyroxide._pyroxide"),
        ),
        (_distribution_identity("pyro3"),),
    )
    if not native_library.is_file():
        return ComparisonCell(
            id="pyroxide-dylib-scheduled",
            artifact_hash=artifact_hash,
            unavailable_reason=library_reason,
        )
    if importlib.util.find_spec("pyroxide") is None:
        return ComparisonCell(
            id="pyroxide-dylib-scheduled",
            artifact_hash=artifact_hash,
            unavailable_reason="Pyroxide is not installed in this interpreter",
        )
    return ComparisonCell(
        id="pyroxide-dylib-scheduled",
        artifact_hash=artifact_hash,
        run=_lazy(_pyroxide_dylib_factory(native_library)),
    )


def _pyroxide_dylib_factory(
    native_library: Path,
) -> Callable[[], Callable[[bytes], bytes]]:
    def factory() -> Callable[[bytes], bytes]:
        import pyroxide

        library_name = "benchmark_native_" + hashlib.sha256(
            native_library.read_bytes()
        ).hexdigest()[:16]
        pyroxide.register_dylib(
            library_name,
            str(native_library),
            free_fn_name="pyroxide_plugin_free",
        )
        proxy = pyroxide.load_dylib(
            library_name,
            free_fn_name="pyroxide_plugin_free",
        )

        def run(payload: bytes) -> bytes:
            return bytes(proxy.pyroxide_plugin_run(payload).result())

        return run

    return factory


def _pyroxide_wasm_factory(
    wasm_bytes: bytes,
) -> Callable[[], Callable[[bytes], bytes]]:
    def factory() -> Callable[[bytes], bytes]:
        import pyroxide

        module_name = "benchmark_wasm_" + hashlib.sha256(wasm_bytes).hexdigest()[:16]
        pyroxide.register_wasm(module_name, wasm_bytes)

        @pyroxide.wasm_task(module_name, "run")
        def call_guest(_: bytes) -> bytes:
            raise AssertionError("Pyroxide must route this call to the WASM guest")

        def run(payload: bytes) -> bytes:
            with pyroxide.scoped(
                wasm_memory_limit_bytes=plugin_backends.DEFAULT_WASM_MEMORY_LIMIT_BYTES
            ):
                return bytes(call_guest(payload).result())

        return run

    return factory


def _wasmtime_warm_factory(
    wasm_bytes: bytes,
) -> Callable[[], Callable[[bytes], bytes]]:
    def factory() -> Callable[[bytes], bytes]:
        import wasmtime

        config = wasmtime.Config()
        config.epoch_interruption = True
        engine = wasmtime.Engine(config)
        module = wasmtime.Module(engine, wasm_bytes)
        store = wasmtime.Store(engine)
        store.set_epoch_deadline(1_000_000_000)
        store.set_limits(memory_size=plugin_backends.DEFAULT_WASM_MEMORY_LIMIT_BYTES)
        instance = wasmtime.Instance(store, module, [])
        exports = instance.exports(store)
        memory = exports["memory"]
        alloc = exports["alloc"]
        dealloc = exports["dealloc"]
        guest_run = exports["run"]

        def run(payload: bytes) -> bytes:
            if len(payload) > plugin_backends.DEFAULT_WASM_MEMORY_LIMIT_BYTES:
                raise plugin_backends.WasmSizeLimitError("WASM input exceeds memory limit")
            input_pointer = alloc(store, len(payload))
            output_pointer = 0
            output_length = 0
            try:
                memory.write(store, payload, input_pointer)
                packed = guest_run(store, input_pointer, len(payload))
                output_pointer = packed >> 32
                output_length = packed & 0xFFFF_FFFF
                plugin_backends._validate_wasm_output(
                    memory,
                    store,
                    output_pointer,
                    output_length,
                    plugin_backends.DEFAULT_WASM_MEMORY_LIMIT_BYTES,
                )
                return bytes(
                    memory.read(store, output_pointer, output_pointer + output_length)
                )
            finally:
                dealloc(store, input_pointer, len(payload))
                if output_pointer and output_length:
                    dealloc(store, output_pointer, output_length)

        return run

    return factory


def _lazy(
    factory: Callable[[], Callable[[bytes], bytes]],
) -> Callable[[bytes], bytes]:
    runner: Callable[[bytes], bytes] | None = None

    def run(payload: bytes) -> bytes:
        nonlocal runner
        if runner is None:
            runner = factory()
        return runner(payload)

    return run


def _process_tree_rss_bytes() -> int:
    try:
        import psutil
    except ModuleNotFoundError:
        # Every boundary cell in this controller is in-process. With no child
        # processes, the process-tree peak is the current process peak.
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return peak if sys.platform == "darwin" else peak * 1024
    process = psutil.Process()
    processes = (process, *process.children(recursive=True))
    total = 0
    for observed in processes:
        try:
            total += observed.memory_info().rss
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return total


def _environment_label() -> str:
    gil_probe = getattr(sys, "_is_gil_enabled", None)
    gil = "unknown" if gil_probe is None else ("enabled" if gil_probe() else "disabled")
    return (
        f"{platform.python_implementation()}-{platform.python_version()}-"
        f"{platform.system()}-{platform.machine()}-gil-{gil}"
    )


def _hash_paths(paths: Sequence[Path]) -> str:
    return _hash_artifacts(paths, ())


def _hash_artifacts(
    paths: Sequence[Path],
    identifiers: Sequence[str],
) -> str:
    digest = hashlib.sha256()
    found = False
    for path in sorted(paths, key=lambda candidate: str(candidate)):
        if not path.is_file():
            continue
        found = True
        digest.update(str(path.name).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    for identifier in sorted(identifiers):
        found = True
        digest.update(identifier.encode("utf-8"))
        digest.update(b"\0")
    if not found:  # pragma: no cover - this module is always one of the inputs.
        digest.update(b"unavailable")
    return digest.hexdigest()


def _distribution_identity(name: str) -> str:
    try:
        version = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        version = "unavailable"
    return f"{name}={version}"


def _module_path(name: str) -> Path:
    try:
        spec = importlib.util.find_spec(name)
    except ModuleNotFoundError:
        spec = None
    if spec is None or spec.origin is None:
        return Path(f"{name}-unavailable")
    return Path(spec.origin)


def _require_table(value: object, section: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{section} must be a TOML table")
    return value


def _require_exact_keys(
    values: Mapping[str, object],
    expected: set[str],
    section: str,
) -> None:
    if set(values) != expected:
        raise ValueError(f"{section} has missing or unknown keys")


if __name__ == "__main__":  # pragma: no cover - command-line entry point.
    raise SystemExit(main())
