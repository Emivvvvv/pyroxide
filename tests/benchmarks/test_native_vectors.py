from __future__ import annotations

import ctypes
import hashlib
import importlib
import importlib.util
import os
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from examples.benchmarks import plugin_backends, plugin_runner
from examples.benchmarks.native_bindings import (
    cffi_adapter,
    ctypes_adapter,
    nanobind,
    pyo3,
)

_ROOT = Path(__file__).resolve().parents[2]
_CORE_MANIFEST = _ROOT / "examples" / "benchmarks" / "native_core" / "Cargo.toml"
_PYO3_MANIFEST = (
    _ROOT / "examples" / "benchmarks" / "native_bindings" / "pyo3" / "Cargo.toml"
)
_FRAME_VERSION = 1
_FRAME_BYTES = 52
_MIX_MULTIPLIER_1 = 0xBF58476D1CE4E5B9
_MIX_MULTIPLIER_2 = 0x94D049BB133111EB
_MIX_SEED = 0x9E3779B97F4A7C15
_MASK_64 = (1 << 64) - 1


def _reference_mix(payload: bytes) -> int:
    """Independent Task 2-compatible implementation of the published mixing contract."""
    state = _MIX_SEED
    for value in payload:
        state ^= value
        state = (state * _MIX_MULTIPLIER_1) & _MASK_64
        state ^= state >> 31
        state = (state * _MIX_MULTIPLIER_2) & _MASK_64
        state ^= state >> 27
    return state


def _expected_frame(payload: bytes) -> bytes:
    return struct.pack(
        "<IQQ",
        _FRAME_VERSION,
        len(payload),
        _reference_mix(payload),
    ) + hashlib.sha256(payload).digest()


@pytest.fixture(scope="session")
def native_library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build only the local deterministic ABI fixture; no benchmark cell is run."""
    assert _CORE_MANIFEST.is_file(), "native core manifest is required for ABI vectors"
    target_directory = tmp_path_factory.mktemp("native-core-target")
    environment = {**os.environ, "CARGO_TARGET_DIR": str(target_directory)}
    subprocess.run(
        ["cargo", "build", "--offline", "--release", "--manifest-path", str(_CORE_MANIFEST)],
        check=True,
        cwd=_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    suffix = ".dylib" if os.name == "posix" and sys_platform() == "darwin" else ".so"
    if os.name == "nt":
        suffix = ".dll"
    prefix = "" if os.name == "nt" else "lib"
    artifact = target_directory / "release" / f"{prefix}benchmark_core{suffix}"
    assert artifact.is_file(), "cargo did not produce the native ABI library"
    return artifact


def sys_platform() -> str:
    return sys.platform


@pytest.fixture(scope="session")
def pyo3_module(tmp_path_factory: pytest.TempPathFactory):
    """Build and import the local PyO3 wheel without installing it into the test environment."""
    if importlib.util.find_spec("maturin") is None:
        pytest.skip("maturin is unavailable; the PyO3 build definition cannot be exercised")
    wheel_directory = tmp_path_factory.mktemp("pyo3-wheel")
    target_directory = tmp_path_factory.mktemp("pyo3-target")
    environment = {**os.environ, "CARGO_TARGET_DIR": str(target_directory)}
    subprocess.run(
        [
            sys.executable,
            "-m",
            "maturin",
            "build",
            "--release",
            "--manifest-path",
            str(_PYO3_MANIFEST),
            "--interpreter",
            sys.executable,
            "--out",
            str(wheel_directory),
        ],
        check=True,
        cwd=_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_directory.glob("benchmark_pyo3-*.whl"))
    extraction_directory = tmp_path_factory.mktemp("pyo3-wheel-content")
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(extraction_directory)
    sys.path.insert(0, str(extraction_directory))
    return importlib.import_module("benchmark_pyo3")


@pytest.mark.parametrize("payload", [b"", b"benchmark\x00payload", bytes(range(32))])
def test_ctypes_and_plugin_exports_match_golden_abi_vectors(
    native_library: Path,
    payload: bytes,
) -> None:
    """Changing byte order, digest, or exported plugin forwarding must fail this ABI oracle."""
    expected = _expected_frame(payload)

    neutral = ctypes_adapter.run(native_library, payload)
    plugin = ctypes_adapter.run_plugin(native_library, payload)

    assert neutral == expected
    assert plugin == expected
    assert len(neutral) == _FRAME_BYTES
    version, input_length, _ = struct.unpack("<IQQ", neutral[:20])
    assert version == _FRAME_VERSION
    assert input_length == len(payload)


def test_native_abi_allocates_and_frees_each_owned_output_buffer(native_library: Path) -> None:
    """Changing allocation ownership or its matching free function must fail this ABI round-trip."""
    library = ctypes.CDLL(str(native_library))
    library.benchmark_run.argtypes = [
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    library.benchmark_run.restype = ctypes.POINTER(ctypes.c_ubyte)
    library.benchmark_free.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t]
    library.benchmark_last_error.restype = ctypes.c_int
    payload = b"ownership"
    input_buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
    output_length = ctypes.c_size_t()

    output_pointer = library.benchmark_run(
        input_buffer,
        len(payload),
        ctypes.byref(output_length),
    )

    assert bytes(ctypes.string_at(output_pointer, output_length.value)) == _expected_frame(
        payload
    )
    library.benchmark_free(output_pointer, output_length.value)
    assert library.benchmark_last_error() == 0
    artifact = ctypes_adapter.artifact_metadata(native_library)
    assert artifact["sha256"] == hashlib.sha256(native_library.read_bytes()).hexdigest()
    assert artifact["size_bytes"] == native_library.stat().st_size


def test_native_abi_rejects_invalid_pointers_with_documented_error_codes(
    native_library: Path,
) -> None:
    """Dereferencing a null input or output-length pointer must fail before native reads."""
    library = ctypes.CDLL(str(native_library))
    library.benchmark_run.argtypes = [
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    library.benchmark_run.restype = ctypes.POINTER(ctypes.c_ubyte)
    library.benchmark_last_error.restype = ctypes.c_int

    output_length = ctypes.c_size_t(123)
    null_result = library.benchmark_run(None, 1, ctypes.byref(output_length))

    assert not null_result
    assert output_length.value == 0
    assert library.benchmark_last_error() == ctypes_adapter.ERROR_NULL_INPUT
    assert not library.benchmark_run(None, 0, None)
    assert library.benchmark_last_error() == ctypes_adapter.ERROR_NULL_OUTPUT_LENGTH


def test_cffi_adapter_matches_ctypes_when_cffi_is_available(native_library: Path) -> None:
    """Changing the CFFI declaration or ownership handling must diverge from the C ABI."""
    if not cffi_adapter.is_available():
        pytest.skip(cffi_adapter.unavailable_reason())

    payload = b"cffi-vector"

    assert cffi_adapter.run(native_library, payload) == ctypes_adapter.run(
        native_library,
        payload,
    )


def test_cffi_adapter_reuses_one_warmed_binding(native_library: Path) -> None:
    """Recreating FFI declarations or dlopen state per call must break binding identity."""
    if not cffi_adapter.is_available():
        pytest.skip(cffi_adapter.unavailable_reason())

    binding = cffi_adapter.bind(native_library)

    assert cffi_adapter.bind(native_library) is binding
    assert binding(b"first") == _expected_frame(b"first")
    assert binding(b"second") == _expected_frame(b"second")


def test_plugin_runner_times_the_warmed_cffi_binding(native_library: Path) -> None:
    """Routing timed calls through setup-heavy adapter.run must fail this cell contract."""
    if not cffi_adapter.is_available():
        pytest.skip(cffi_adapter.unavailable_reason())

    cell = plugin_runner.build_native_cells(native_library)["cffi-direct"]

    assert cell.run is cffi_adapter.bind(native_library)


def test_pyo3_adapter_matches_golden_vectors_and_releases_the_gil(
    native_library: Path,
    pyo3_module,
) -> None:
    """Changing the PyO3 core path or its GIL policy must fail this direct-binding check."""
    payload = b"binding-vector"

    assert pyo3.is_available()
    assert pyo3.run(payload, native_library) == _expected_frame(payload)
    assert "detach" in pyo3_module.gil_policy()


def test_nanobind_adapter_has_explicit_availability_behavior(native_library: Path) -> None:
    """An unavailable nanobind toolchain must skip instead of returning substituted bytes."""
    if not nanobind.is_available():
        pytest.skip(nanobind.unavailable_reason())

    assert nanobind.run(b"binding-vector", native_library) == _expected_frame(
        b"binding-vector"
    )


def test_native_backend_labels_keep_execution_layers_distinct() -> None:
    """Calling bindings task schedulers would collapse distinct benchmark semantics."""
    labels = plugin_backends.native_backend_labels()

    assert [label.id for label in labels] == [
        "native_direct_call",
        "native_binding_thread_pool",
        "pyroxide_plugin_scheduling",
    ]
    assert all("pyo3" not in label.execution_model for label in labels)
    assert all("nanobind" not in label.execution_model for label in labels)
    assert labels[0].execution_model != labels[1].execution_model != labels[2].execution_model
