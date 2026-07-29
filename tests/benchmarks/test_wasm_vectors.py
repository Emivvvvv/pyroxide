from __future__ import annotations

import hashlib
import os
import struct
import subprocess
from pathlib import Path

import pytest

from examples.benchmarks import plugin_backends

_ROOT = Path(__file__).resolve().parents[2]
_WASM_MANIFEST = _ROOT / "examples" / "benchmarks" / "wasm_core" / "Cargo.toml"
_FRAME_VERSION = 1
_FRAME_BYTES = 52
_MIX_MULTIPLIER_1 = 0xBF58476D1CE4E5B9
_MIX_MULTIPLIER_2 = 0x94D049BB133111EB
_MIX_SEED = 0x9E3779B97F4A7C15
_MASK_64 = (1 << 64) - 1


def _reference_mix(payload: bytes) -> int:
    """Independent Task 2-compatible mixing oracle for the common ABI frame."""
    state = _MIX_SEED
    for value in payload:
        state ^= value
        state = (state * _MIX_MULTIPLIER_1) & _MASK_64
        state ^= state >> 31
        state = (state * _MIX_MULTIPLIER_2) & _MASK_64
        state ^= state >> 27
    return state


def _expected_frame(payload: bytes) -> bytes:
    return struct.pack("<IQQ", _FRAME_VERSION, len(payload), _reference_mix(payload)) + (
        hashlib.sha256(payload).digest()
    )


@pytest.fixture(scope="session")
def wasm_artifact(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Compile the local guest once for bounded correctness checks; no benchmark runs."""
    assert _WASM_MANIFEST.is_file(), "WASM core manifest is required for byte vectors"
    target_directory = tmp_path_factory.mktemp("wasm-core-target")
    environment = {**os.environ, "CARGO_TARGET_DIR": str(target_directory)}
    try:
        subprocess.run(
            [
                "cargo",
                "build",
                "--offline",
                "--release",
                "--target",
                "wasm32-unknown-unknown",
                "--manifest-path",
                str(_WASM_MANIFEST),
            ],
            check=True,
            cwd=_ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        subprocess.run(
            [
                "cargo",
                "build",
                "--release",
                "--target",
                "wasm32-unknown-unknown",
                "--manifest-path",
                str(_WASM_MANIFEST),
            ],
            check=True,
            cwd=_ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
    artifact = (
        target_directory
        / "wasm32-unknown-unknown"
        / "release"
        / "benchmark_wasm_core.wasm"
    )
    assert artifact.is_file(), "cargo did not produce the WASM guest artifact"
    return artifact


@pytest.mark.parametrize("payload", [b"", b"benchmark\x00payload", bytes(range(32))])
def test_pyroxide_wasm_guest_matches_native_byte_vectors(
    wasm_artifact: Path,
    payload: bytes,
) -> None:
    """Changing the guest byte order, digest, or packed result ABI must fail this oracle."""
    if not plugin_backends.pyroxide_wasm_available():
        pytest.skip(plugin_backends.pyroxide_wasm_unavailable_reason())
    wasm_bytes = wasm_artifact.read_bytes()

    result = plugin_backends.run_pyroxide_wasm(wasm_bytes, payload)

    assert wasm_bytes[:4] == b"\x00asm"
    assert result == _expected_frame(payload)
    assert len(result) == _FRAME_BYTES


def test_pyroxide_wasm_reports_guest_traps(wasm_artifact: Path) -> None:
    """Replacing a guest trap with a successful result must fail this safety contract."""
    if not plugin_backends.pyroxide_wasm_available():
        pytest.skip(plugin_backends.pyroxide_wasm_unavailable_reason())
    with pytest.raises(RuntimeError, match="WASM execution failed"):
        plugin_backends.run_pyroxide_wasm(
            wasm_artifact.read_bytes(),
            b"trap-vector",
            function_name="trap",
        )


def test_pyroxide_wasm_rejects_payloads_over_the_guest_memory_limit(
    wasm_artifact: Path,
) -> None:
    """Accepting an input beyond the configured guest bound must fail before guest allocation."""
    if not plugin_backends.pyroxide_wasm_available():
        pytest.skip(plugin_backends.pyroxide_wasm_unavailable_reason())
    with pytest.raises(RuntimeError, match="exceeds memory limit"):
        plugin_backends.run_pyroxide_wasm(
            wasm_artifact.read_bytes(),
            b"x" * (plugin_backends.DEFAULT_WASM_MEMORY_LIMIT_BYTES + 1),
            memory_limit_bytes=plugin_backends.DEFAULT_WASM_MEMORY_LIMIT_BYTES,
        )


def test_direct_wasmtime_matches_the_guest_contract_when_available(
    wasm_artifact: Path,
) -> None:
    """A direct host must produce identical bytes or state why it cannot run the guest."""
    if not plugin_backends.wasmtime_available():
        pytest.skip(plugin_backends.wasmtime_unavailable_reason())

    payload = b"wasmtime-vector"
    assert plugin_backends.run_wasmtime_wasm(wasm_artifact.read_bytes(), payload) == (
        _expected_frame(payload)
    )


def test_direct_wasmtime_enforces_the_same_input_limit_when_available(
    wasm_artifact: Path,
) -> None:
    """A direct host must reject the same bounded input before it calls the guest."""
    if not plugin_backends.wasmtime_available():
        pytest.skip(plugin_backends.wasmtime_unavailable_reason())

    with pytest.raises(plugin_backends.WasmSizeLimitError, match="exceeds memory limit"):
        plugin_backends.run_wasmtime_wasm(
            wasm_artifact.read_bytes(),
            b"x" * (plugin_backends.DEFAULT_WASM_MEMORY_LIMIT_BYTES + 1),
            memory_limit_bytes=plugin_backends.DEFAULT_WASM_MEMORY_LIMIT_BYTES,
        )


def test_wasm_labels_and_host_metadata_keep_cold_warm_and_scheduler_paths_distinct() -> None:
    """Combining compilation, instantiation, calls, or scheduling would invalidate comparisons."""
    labels = plugin_backends.wasm_backend_labels()
    settings = plugin_backends.wasmtime_host_settings()

    assert [label.id for label in labels] == [
        "wasm_cold_compile_instantiate",
        "wasm_warm_call",
        "pyroxide_wasm_scheduling",
    ]
    assert labels[0].execution_model != labels[1].execution_model
    assert labels[1].execution_model != labels[2].execution_model
    assert settings.pyroxide_wasmtime_version == "36.0.12"
    assert settings.epoch_interruption is True
    assert plugin_backends.extism_compatible_with_guest_abi() is False
    assert plugin_backends.extism_exclusion_reason()


def test_wasm_artifact_metadata_records_hash_and_size(wasm_artifact: Path) -> None:
    """Dropping the artifact hash would make the guest identity unverifiable."""
    metadata = plugin_backends.wasm_artifact_metadata(wasm_artifact)

    assert metadata["sha256"] == hashlib.sha256(wasm_artifact.read_bytes()).hexdigest()
    assert metadata["size_bytes"] == wasm_artifact.stat().st_size
