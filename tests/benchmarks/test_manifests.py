"""Static validation for bounded, semantically separate experiment profiles."""

from __future__ import annotations

from pathlib import Path

from examples.benchmarks import profile_manifest
from examples.odoo_benchmark import runner as odoo_runner

ROOT = Path(__file__).parents[2]
MANIFESTS = ROOT / "examples/benchmarks/manifests"
ADDON = ROOT / "examples/odoo_benchmark/addons/pyroxide_benchmark"


def test_all_profiles_are_versioned_unique_and_static_only() -> None:
    """Removing semantic labels or duplicate-cell protection must fail this matrix check."""
    profiles = {
        name: profile_manifest.load_profile(MANIFESTS / f"{name}.toml")
        for name in (
            "exploratory",
            "paper",
            "soak",
            "python-history",
            "python-314",
            "python-314t",
            "distributed",
        )
    }

    assert {profile["profile"]["output_label"] for profile in profiles.values()} == {
        "current_full",
        "distributed_separate",
        "exploratory",
        "free_threaded_compatible_only",
        "historical_reduced",
        "paper",
        "reliability_separate",
    }
    assert all(profile["schema_version"] == 1 for profile in profiles.values())
    assert all(profile_manifest.count_cells(profile) == len(profile["experiments"]) for profile in profiles.values())
    assert all(profile_manifest.experiment_manifest(profile).experiments for profile in profiles.values())


def test_python_history_is_reduced_and_covers_cpython_310_through_regular_314() -> None:
    """Dropping a historical interpreter or adding an unmatched backend must fail."""
    profile = profile_manifest.load_profile(MANIFESTS / "python-history.toml")

    assert profile["matrix"]["python_versions"] == ["3.10", "3.11", "3.12", "3.13", "3.14"]
    assert {backend["kind"] for backend in profile["backends"]} == {
        "pyroxide",
        "thread_pool",
        "process_pool",
    }
    assert profile_manifest.count_cells(profile) == 6
    assert {backend["workers"] for backend in profile["backends"]} == {4}


def test_current_and_free_threaded_profiles_enforce_availability_and_gil_semantics() -> None:
    """Treating optional extensions as available or allowing a GIL-enabled 314t run must fail."""
    current = profile_manifest.load_profile(MANIFESTS / "python-314.toml")
    free_threaded = profile_manifest.load_profile(MANIFESTS / "python-314t.toml")

    assert {backend["kind"] for backend in current["backends"]} >= {
        "interpreter_pool",
        "loky",
        "joblib",
    }
    assert current["availability"]["optional_backends"] == {
        "interpreter_pool": "required_builtin",
        "joblib": "optional_must_be_present",
        "loky": "optional_must_be_present",
    }
    assert free_threaded["requirements"]["post_import_gil"] == "abort_if_enabled"
    assert {backend["kind"] for backend in free_threaded["backends"]} == {
        "process_pool",
        "pyroxide_isolated",
        "pyroxide_threaded",
        "thread_pool",
    }
    assert free_threaded["availability"]["extension_compatibility"] == {
        "cffi_native": "unavailable_until_verified",
        "ctypes_native": "unavailable_until_verified",
        "pyo3_native": "unavailable_until_verified",
    }


def test_distributed_profile_cannot_be_ranked_with_local_executor_tables() -> None:
    """Merging Ray or Dask measurements into local results must fail this profile contract."""
    profile = profile_manifest.load_profile(MANIFESTS / "distributed.toml")

    assert profile["profile"]["comparison_scope"] == "separate_from_local_executor_tables"
    assert {backend["kind"] for backend in profile["backends"]} == {
        "dask_single_node",
        "ray_single_node",
    }
    assert profile["availability"]["optional_backends"] == {
        "dask_single_node": "optional_must_be_present",
        "ray_single_node": "optional_must_be_present",
    }


def test_exploratory_paper_and_reliability_profiles_have_required_sample_boundaries() -> None:
    """Reducing sufficiency or mixing recovery costs into steady state must fail."""
    exploratory = profile_manifest.load_profile(MANIFESTS / "exploratory.toml")
    paper = profile_manifest.load_profile(MANIFESTS / "paper.toml")
    soak = profile_manifest.load_profile(MANIFESTS / "soak.toml")

    assert exploratory["matrix"]["worker_levels"] == [1, 2, 4, "physical_cores"]
    assert exploratory["matrix"]["payload_bytes"] == [1024, 1_048_576, 16_777_216]
    assert exploratory["requirements"]["fresh_process_blocks"] == 5
    assert exploratory["requirements"]["calibrated_operations"] >= 100
    assert paper["requirements"] == {
        "complete_checksums": True,
        "fresh_process_blocks": 30,
        "macro_observations": 30,
    }
    assert profile_manifest.count_cells(paper) == 8
    assert soak["requirements"] == {
        "fresh_process_blocks": 5,
        "rc_soak_minutes": 5,
        "final_soak_hours": 8,
    }
    assert {experiment["id"] for experiment in soak["experiments"]} == {
        "cancellation-reliability",
        "crash-reliability",
        "rc-soak-5m",
        "recycling-reliability",
        "saturation-reliability",
    }
    assert {experiment["profile"] for experiment in soak["experiments"]} == {"reliability"}


def test_addon_tree_hash_is_deterministic_and_unblocks_integrated_odoo_validation(
    tmp_path: Path,
) -> None:
    """Changing one add-on byte or retaining the unresolved sentinel must fail validation."""
    fixture = tmp_path / "addon"
    fixture.mkdir()
    (fixture / "z.py").write_bytes(b"z")
    nested = fixture / "nested"
    nested.mkdir()
    (nested / "a.txt").write_bytes(b"a")

    first = odoo_runner.addon_tree_sha256(fixture)
    (nested / "a.txt").write_bytes(b"changed")

    assert first == "25859ff2ef9bd32d2c7441a2be31d0b6df469483cc4b00ca8af99ea7ede55c32"
    assert odoo_runner.addon_tree_sha256(fixture) != first
    versions = odoo_runner.load_versions(ROOT / "examples/odoo_benchmark/versions.toml")
    profile = odoo_runner.load_profile(ROOT / "examples/odoo_benchmark/manifests/odoo19-py314.toml")
    assert versions["addon"]["addon_tree_sha256"] == odoo_runner.addon_tree_sha256(ADDON)
    assert odoo_runner.validate_profile(profile, versions) == []
