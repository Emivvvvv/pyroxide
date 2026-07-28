"""Versioned benchmark configuration and observation contracts."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10 only.
    import tomli as tomllib


SCHEMA_VERSION = 1

_BACKEND_KINDS = frozenset(
    {
        "pyroxide",
        "pyroxide_threaded",
        "pyroxide_isolated",
        "thread_pool",
        "process_pool",
        "interpreter_pool",
        "loky",
        "joblib",
        "ray_single_node",
        "dask_single_node",
        "free_threaded_python",
    }
)
_EXPERIMENT_PROFILES = frozenset({"correctness_only", "reliability", "steady_state"})
_REPORT_PERCENTILES = frozenset({"p50", "p95", "p99"})


def _require_non_empty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _require_positive_int(value: object, field: str) -> int:
    value = _require_int(value, field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _validate_keys(
    values: Mapping[str, Any],
    *,
    section: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"{section} contains unknown keys: {', '.join(unknown)}")
    missing = sorted(required - set(values))
    if missing:
        raise ValueError(f"{section} is missing required keys: {', '.join(missing)}")


@dataclass(frozen=True, slots=True)
class WorkloadSpec:
    id: str
    kind: str
    tasks_per_sample: int
    payload_bytes: int

    def __post_init__(self) -> None:
        _require_non_empty_string(self.id, "workload id")
        _require_non_empty_string(self.kind, "workload kind")
        _require_positive_int(self.tasks_per_sample, "tasks_per_sample")
        payload_bytes = _require_int(self.payload_bytes, "payload_bytes")
        if payload_bytes < 0:
            raise ValueError("payload_bytes must not be negative")


@dataclass(frozen=True, slots=True)
class BackendSpec:
    id: str
    kind: str
    workers: int
    recycle_workers: bool = False

    def __post_init__(self) -> None:
        _require_non_empty_string(self.id, "backend id")
        kind = _require_non_empty_string(self.kind, "backend kind")
        if kind not in _BACKEND_KINDS:
            raise ValueError(f"unknown backend kind: {kind}")
        _require_positive_int(self.workers, "workers")
        if not isinstance(self.recycle_workers, bool):
            raise ValueError("recycle_workers must be a boolean")


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    id: str
    workload: str
    backend: str
    profile: str
    random_seed: int
    repetitions: int | None = None
    report_percentiles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty_string(self.id, "experiment id")
        _require_non_empty_string(self.workload, "experiment workload")
        _require_non_empty_string(self.backend, "experiment backend")
        profile = _require_non_empty_string(self.profile, "experiment profile")
        if profile not in _EXPERIMENT_PROFILES:
            raise ValueError(f"unknown experiment profile: {profile}")
        _require_int(self.random_seed, "random_seed")

        if self.repetitions is not None:
            _require_positive_int(self.repetitions, "repetitions")
        if profile == "steady_state" and self.repetitions is None:
            raise ValueError("steady_state experiments require repetitions")

        unknown_percentiles = sorted(set(self.report_percentiles) - _REPORT_PERCENTILES)
        if unknown_percentiles:
            raise ValueError(
                "unknown report percentiles: " + ", ".join(unknown_percentiles)
            )
        if "p99" in self.report_percentiles and (
            self.repetitions is None or self.repetitions < 1_000
        ):
            raise ValueError("p99 requires at least 1,000 observations")


@dataclass(frozen=True, slots=True)
class ResourceSample:
    rss_bytes: int
    cpu_time_seconds: float

    def __post_init__(self) -> None:
        rss_bytes = _require_int(self.rss_bytes, "rss_bytes")
        if rss_bytes < 0:
            raise ValueError("rss_bytes must not be negative")
        if isinstance(self.cpu_time_seconds, bool) or not isinstance(
            self.cpu_time_seconds, (int, float)
        ):
            raise ValueError("cpu_time_seconds must be a number")
        if isinstance(self.cpu_time_seconds, float) and not math.isfinite(
            self.cpu_time_seconds
        ):
            raise ValueError("cpu_time_seconds must be finite")
        if self.cpu_time_seconds < 0:
            raise ValueError("cpu_time_seconds must not be negative")


@dataclass(frozen=True, slots=True)
class TrialObservation:
    experiment_id: str
    trial_index: int
    latency_samples_seconds: tuple[float, ...]
    resource_samples: tuple[ResourceSample, ...]

    def __post_init__(self) -> None:
        _require_non_empty_string(self.experiment_id, "experiment_id")
        trial_index = _require_int(self.trial_index, "trial_index")
        if trial_index < 0:
            raise ValueError("trial_index must not be negative")
        for latency in self.latency_samples_seconds:
            if isinstance(latency, bool) or not isinstance(latency, (int, float)):
                raise ValueError("latency_samples_seconds must contain numbers")
            if isinstance(latency, float) and not math.isfinite(latency):
                raise ValueError("latency_samples_seconds must contain finite numbers")
            if latency < 0:
                raise ValueError("latency_samples_seconds must not contain negatives")

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "experiment_id": self.experiment_id,
                "trial_index": self.trial_index,
                "latency_samples_seconds": list(self.latency_samples_seconds),
                "resource_samples": [asdict(sample) for sample in self.resource_samples],
            },
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    workloads: tuple[WorkloadSpec, ...]
    backends: tuple[BackendSpec, ...]
    experiments: tuple[ExperimentSpec, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")
        _ensure_unique_ids(self.workloads, "workload")
        _ensure_unique_ids(self.backends, "backend")
        _ensure_unique_ids(self.experiments, "experiment")

        workload_ids = {workload.id for workload in self.workloads}
        backends = {backend.id: backend for backend in self.backends}
        for experiment in self.experiments:
            if experiment.workload not in workload_ids:
                raise ValueError(f"unknown workload: {experiment.workload}")
            backend = backends.get(experiment.backend)
            if backend is None:
                raise ValueError(f"unknown backend: {experiment.backend}")
            if experiment.profile == "steady_state" and backend.recycle_workers:
                raise ValueError("steady_state experiments cannot recycle workers")

    @classmethod
    def from_toml(cls, path: str | Path) -> ExperimentManifest:
        with Path(path).open("rb") as manifest_file:
            values = tomllib.load(manifest_file)
        _validate_keys(
            values,
            section="manifest",
            required={"schema_version", "workloads", "backends", "experiments"},
        )
        schema_version = _require_int(values["schema_version"], "schema_version")
        if schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {schema_version}")
        return cls(
            schema_version=schema_version,
            workloads=tuple(_parse_workload(value) for value in values["workloads"]),
            backends=tuple(_parse_backend(value) for value in values["backends"]),
            experiments=tuple(
                _parse_experiment(value) for value in values["experiments"]
            ),
        )


def _ensure_unique_ids(specifications: tuple[Any, ...], kind: str) -> None:
    ids = [specification.id for specification in specifications]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {kind} id")


def _require_table(value: object, section: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{section} must be a TOML table")
    return value


def _parse_workload(value: object) -> WorkloadSpec:
    values = _require_table(value, "workload")
    _validate_keys(
        values,
        section="workload",
        required={"id", "kind", "tasks_per_sample", "payload_bytes"},
    )
    return WorkloadSpec(
        id=values["id"],
        kind=values["kind"],
        tasks_per_sample=values["tasks_per_sample"],
        payload_bytes=values["payload_bytes"],
    )


def _parse_backend(value: object) -> BackendSpec:
    values = _require_table(value, "backend")
    _validate_keys(
        values,
        section="backend",
        required={"id", "kind", "workers"},
        optional={"recycle_workers"},
    )
    return BackendSpec(
        id=values["id"],
        kind=values["kind"],
        workers=values["workers"],
        recycle_workers=values.get("recycle_workers", False),
    )


def _parse_experiment(value: object) -> ExperimentSpec:
    values = _require_table(value, "experiment")
    _validate_keys(
        values,
        section="experiment",
        required={"id", "workload", "backend", "profile", "random_seed"},
        optional={"repetitions", "report_percentiles"},
    )
    percentiles = values.get("report_percentiles", [])
    if not isinstance(percentiles, list) or not all(
        isinstance(percentile, str) for percentile in percentiles
    ):
        raise ValueError("report_percentiles must be an array of strings")
    return ExperimentSpec(
        id=values["id"],
        workload=values["workload"],
        backend=values["backend"],
        profile=values["profile"],
        random_seed=values["random_seed"],
        repetitions=values.get("repetitions"),
        report_percentiles=tuple(percentiles),
    )
