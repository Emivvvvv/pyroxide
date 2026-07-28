"""Strict static validation for bounded benchmark experiment profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10 only.
    import tomli as tomllib

try:
    from .models import BackendSpec, ExperimentManifest, ExperimentSpec, WorkloadSpec
except ImportError:  # pragma: no cover - exercised by script entry points.
    from models import BackendSpec, ExperimentManifest, ExperimentSpec, WorkloadSpec

PROFILE_SCHEMA_VERSION = 1
_ROOT_FIELDS = {
    "schema_version",
    "profile",
    "requirements",
    "availability",
    "matrix",
    "workloads",
    "backends",
    "experiments",
}
_PROFILE_FIELDS = {"id", "track", "output_label", "comparison_scope"}
_REQUIREMENT_FIELDS = {
    "calibrated_operations",
    "complete_checksums",
    "fixed_affinity",
    "fresh_process_blocks",
    "latency_observations",
    "macro_observations",
    "post_import_gil",
    "power_metadata",
    "rc_soak_hours",
    "sample_seconds_max",
    "sample_seconds_min",
    "soak_minutes",
}
_MATRIX_FIELDS = {"payload_bytes", "python_versions", "task_grains", "worker_levels"}
_AVAILABILITY_FIELDS = {"extension_compatibility", "optional_backends"}


class ProfileManifestError(ValueError):
    """A static profile is incomplete, ambiguous, or semantically invalid."""


def load_profile(path: str | Path) -> dict[str, Any]:
    """Parse and validate one static manifest without executing an experiment cell."""
    with Path(path).open("rb") as profile_file:
        profile = tomllib.load(profile_file)
    _validate_root(profile)
    _validate_mapping(profile["profile"], "profile", _PROFILE_FIELDS, _PROFILE_FIELDS)
    _validate_mapping(profile["requirements"], "requirements", set(), _REQUIREMENT_FIELDS)
    _validate_mapping(profile["availability"], "availability", set(), _AVAILABILITY_FIELDS)
    _validate_mapping(profile["matrix"], "matrix", set(), _MATRIX_FIELDS)
    _validate_model_matrix(profile)
    _validate_semantics(profile)
    return profile


def count_cells(profile: Mapping[str, Any]) -> int:
    """Return the number of unique declared workload/backend/semantic cells."""
    cells = {
        (experiment["workload"], experiment["backend"], experiment["profile"])
        for experiment in profile["experiments"]
    }
    return len(cells)


def experiment_manifest(profile: Mapping[str, Any]) -> ExperimentManifest:
    """Return the executable core after strict profile validation."""
    return ExperimentManifest(
        workloads=tuple(_workload(value) for value in profile["workloads"]),
        backends=tuple(_backend(value) for value in profile["backends"]),
        experiments=tuple(_experiment(value) for value in profile["experiments"]),
    )


def _validate_root(profile: Mapping[str, Any]) -> None:
    if set(profile) != _ROOT_FIELDS:
        raise ProfileManifestError("profile root has missing or unknown fields")
    if profile["schema_version"] != PROFILE_SCHEMA_VERSION:
        raise ProfileManifestError("unsupported profile schema_version")
    for key in ("workloads", "backends", "experiments"):
        if not isinstance(profile[key], list) or not profile[key]:
            raise ProfileManifestError(f"{key} must be a non-empty array")


def _validate_mapping(
    values: object, section: str, required: set[str], allowed: set[str]
) -> None:
    if not isinstance(values, dict):
        raise ProfileManifestError(f"{section} must be a table")
    unknown = set(values) - allowed
    missing = required - set(values)
    if unknown or missing:
        raise ProfileManifestError(f"{section} has missing or unknown fields")


def _validate_model_matrix(profile: Mapping[str, Any]) -> None:
    try:
        experiment_manifest(profile)
    except (KeyError, TypeError, ValueError) as error:
        raise ProfileManifestError(str(error)) from error
    if count_cells(profile) != len(profile["experiments"]):
        raise ProfileManifestError("duplicate workload/backend/semantic experiment cell")
    worker_counts = {backend["workers"] for backend in profile["backends"]}
    if len(worker_counts) != 1:
        raise ProfileManifestError("all compared backends must use matched worker counts")


def _workload(value: object) -> WorkloadSpec:
    _validate_mapping(value, "workload", {"id", "kind", "tasks_per_sample", "payload_bytes"}, {"id", "kind", "tasks_per_sample", "payload_bytes"})
    return WorkloadSpec(**value)


def _backend(value: object) -> BackendSpec:
    _validate_mapping(value, "backend", {"id", "kind", "workers"}, {"id", "kind", "workers", "recycle_workers"})
    return BackendSpec(**value)


def _experiment(value: object) -> ExperimentSpec:
    _validate_mapping(
        value,
        "experiment",
        {"id", "workload", "backend", "profile", "random_seed"},
        {"id", "workload", "backend", "profile", "random_seed", "repetitions", "report_percentiles"},
    )
    values = dict(value)
    values["report_percentiles"] = tuple(values.get("report_percentiles", ()))
    return ExperimentSpec(**values)


def _validate_semantics(profile: Mapping[str, Any]) -> None:
    metadata = profile["profile"]
    requirements = profile["requirements"]
    track = metadata["track"]
    if metadata["output_label"] == "exploratory":
        if requirements.get("fresh_process_blocks") != 5:
            raise ProfileManifestError("exploratory profiles require five fresh-process blocks")
    if metadata["output_label"] == "paper":
        required = {
            "complete_checksums": True,
            "fresh_process_blocks": 30,
            "macro_observations": 30,
        }
        if requirements != required:
            raise ProfileManifestError("paper profile sample sufficiency is incomplete")
    if track == "distributed" and metadata["comparison_scope"] != "separate_from_local_executor_tables":
        raise ProfileManifestError("distributed profiles must remain separate from local tables")
    if metadata["output_label"] == "free_threaded_compatible_only":
        if requirements.get("post_import_gil") != "abort_if_enabled":
            raise ProfileManifestError("free-threaded profile must abort when the GIL remains enabled")
    if track == "reliability":
        if any(experiment["profile"] != "reliability" for experiment in profile["experiments"]):
            raise ProfileManifestError("reliability cells cannot use steady-state semantics")
