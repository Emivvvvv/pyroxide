from __future__ import annotations

import json
from pathlib import Path

from examples.benchmarks import plugin_runner, report


def _digest(character: str) -> str:
    return character * 64


def test_plugin_profile_declares_non_overlapping_boundary_experiments() -> None:
    """Merging cold, warm, direct, or scheduled calls must invalidate the profile."""
    profile_path = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "benchmarks"
        / "manifests"
        / "plugin-boundaries.toml"
    )

    profile = plugin_runner.PluginProfile.from_toml(profile_path)

    assert profile.blocks == 5
    assert profile.iterations == 1_000
    assert [(experiment.id, experiment.backends) for experiment in profile.experiments] == [
        (
            "native-direct-boundaries",
            ("ctypes-direct", "cffi-direct", "pyo3-direct", "nanobind-direct"),
        ),
        ("native-scheduled-boundary", ("pyroxide-dylib-scheduled",)),
        ("wasm-cold-boundary", ("wasmtime-cold",)),
        ("wasm-warm-boundaries", ("wasmtime-warm", "pyroxide-wasm-scheduled")),
    ]
    assert len({experiment.semantics for experiment in profile.experiments}) == 4
    assert {
        experiment.id: experiment.iterations for experiment in profile.experiments
    }["wasm-cold-boundary"] == 1


def test_experiment_jsonl_is_directly_accepted_by_the_report_contract(
    tmp_path: Path,
) -> None:
    """Dropping a required field, checksum, or paired block must break report ingestion."""
    experiment = plugin_runner.PluginExperiment(
        id="fixture-boundary",
        workload="native-frame-32b",
        semantics="batched_mean_direct_call",
        backends=("alpha", "beta"),
    )
    cells = {
        "alpha": plugin_runner.ComparisonCell(
            id="alpha",
            artifact_hash=_digest("a"),
            run=lambda payload: payload[::-1],
        ),
        "beta": plugin_runner.ComparisonCell(
            id="beta",
            artifact_hash=_digest("b"),
            run=lambda payload: payload[::-1],
        ),
    }
    output_path = tmp_path / "fixture.jsonl"

    plugin_runner.run_experiment(
        experiment,
        cells,
        output_path=output_path,
        blocks=3,
        iterations=2,
        payload=b"fixture",
        expected=b"erutxif",
        environment="fixture-python",
    )

    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    summary = report.build_summary(output_path)
    assert len(rows) == 6
    assert all(row["schema_version"] == 1 for row in rows)
    assert all(row["latency_seconds"] > 0 for row in rows)
    assert all(row["throughput_tasks_per_second"] > 0 for row in rows)
    assert summary["cells"]["alpha"]["sample_count"] == 3
    assert summary["cells"]["beta"]["sample_count"] == 3
    assert summary["pairs"][0]["complete_pairs"] == 3


def test_unavailable_backend_emits_explicit_error_rows_without_substitution(
    tmp_path: Path,
) -> None:
    """Replacing an absent backend with a fallback implementation must fail this record."""
    experiment = plugin_runner.PluginExperiment(
        id="fixture-unavailable",
        workload="native-frame-8b",
        semantics="batched_mean_direct_call",
        backends=("missing",),
    )
    output_path = tmp_path / "unavailable.jsonl"
    cells = {
        "missing": plugin_runner.ComparisonCell(
            id="missing",
            artifact_hash=_digest("c"),
            unavailable_reason="nanobind extension is not built",
        )
    }

    plugin_runner.run_experiment(
        experiment,
        cells,
        output_path=output_path,
        blocks=3,
        iterations=1,
        payload=b"fixture",
        expected=b"unused",
        environment="fixture-python",
    )

    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["status"] for row in rows] == ["error", "error", "error"]
    assert {row["error"] for row in rows} == {"nanobind extension is not built"}
    assert all("latency_seconds" not in row for row in rows)


def test_wrong_backend_bytes_are_recorded_as_errors_not_measurements(
    tmp_path: Path,
) -> None:
    """Accepting a semantically wrong adapter output must never create a successful row."""
    experiment = plugin_runner.PluginExperiment(
        id="fixture-wrong-result",
        workload="native-frame-8b",
        semantics="batched_mean_direct_call",
        backends=("broken",),
    )
    output_path = tmp_path / "wrong.jsonl"
    cells = {
        "broken": plugin_runner.ComparisonCell(
            id="broken",
            artifact_hash=_digest("d"),
            run=lambda _payload: b"wrong",
        )
    }

    plugin_runner.run_experiment(
        experiment,
        cells,
        output_path=output_path,
        blocks=3,
        iterations=1,
        payload=b"fixture",
        expected=b"right",
        environment="fixture-python",
    )

    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert all(row["status"] == "error" for row in rows)
    assert all("correctness mismatch" in row["error"] for row in rows)


def test_native_and_wasm_cells_keep_missing_dependencies_gated(tmp_path: Path) -> None:
    """A missing artifact must not silently remove or rename comparison rows."""
    missing_native = tmp_path / "missing-native.so"
    missing_wasm = tmp_path / "missing-guest.wasm"

    native = plugin_runner.build_native_cells(missing_native)
    wasm = plugin_runner.build_wasm_cells(missing_wasm)

    assert set(native) == {
        "ctypes-direct",
        "cffi-direct",
        "pyo3-direct",
        "nanobind-direct",
        "pyroxide-dylib-scheduled",
    }
    assert set(wasm) == {
        "wasmtime-cold",
        "wasmtime-warm",
        "pyroxide-wasm-scheduled",
    }
    assert native["ctypes-direct"].unavailable_reason
    assert wasm["wasmtime-cold"].unavailable_reason


def test_backend_hash_includes_runtime_identity(tmp_path: Path) -> None:
    """Changing only a loaded binding/host version must change backend evidence."""
    adapter = tmp_path / "adapter.py"
    adapter.write_text("def run(): pass\n", encoding="utf-8")

    first = plugin_runner._hash_artifacts((adapter,), ("wasmtime=36.0.7",))
    second = plugin_runner._hash_artifacts((adapter,), ("wasmtime=36.0.12",))

    assert first != second


def test_missing_nested_module_path_remains_availability_gated() -> None:
    """A missing parent package must not crash discovery before an error row is written."""
    path = plugin_runner._module_path("definitely_missing_plugin_runtime.binding")

    assert path.name == "definitely_missing_plugin_runtime.binding-unavailable"
