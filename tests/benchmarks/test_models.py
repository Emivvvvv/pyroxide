import json
from pathlib import Path

import pytest

from examples.benchmarks.models import (
    SCHEMA_VERSION,
    ExperimentManifest,
    ResourceSample,
    TrialObservation,
)


def write_manifest(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "manifest.toml"
    path.write_text(body, encoding="utf-8")
    return path


def valid_manifest(*, backend: str = "pyroxide", workers: int = 1) -> str:
    return f'''\
schema_version = 1

[[workloads]]
id = "echo"
kind = "echo"
tasks_per_sample = 1
payload_bytes = 16

[[backends]]
id = "pyroxide"
kind = "pyroxide"
workers = {workers}

[[experiments]]
id = "echo-smoke"
workload = "echo"
backend = "{backend}"
profile = "correctness_only"
random_seed = 1729
'''


def test_manifest_rejects_unknown_backend(tmp_path: Path) -> None:
    """Changing an experiment to reference an unconfigured backend must fail."""
    path = write_manifest(tmp_path, valid_manifest(backend="mystery"))

    with pytest.raises(ValueError, match="unknown backend"):
        ExperimentManifest.from_toml(path)


@pytest.mark.parametrize("workers", [0, -1])
def test_manifest_rejects_non_positive_workers(tmp_path: Path, workers: int) -> None:
    """Changing the worker count to zero or below must fail."""
    path = write_manifest(tmp_path, valid_manifest(workers=workers))

    with pytest.raises(ValueError, match="workers.*positive"):
        ExperimentManifest.from_toml(path)


@pytest.mark.parametrize("repetitions", [0, -1])
def test_manifest_rejects_non_positive_steady_state_repetitions(
    tmp_path: Path, repetitions: int
) -> None:
    """Changing a timed experiment's repetitions to zero or below must fail."""
    path = write_manifest(
        tmp_path,
        valid_manifest().replace(
            'profile = "correctness_only"',
            f'profile = "steady_state"\nrepetitions = {repetitions}',
        ),
    )

    with pytest.raises(ValueError, match="repetitions.*positive"):
        ExperimentManifest.from_toml(path)


def test_manifest_rejects_p99_without_one_thousand_observations(
    tmp_path: Path,
) -> None:
    """Changing p99 reporting to use fewer than 1,000 observations must fail."""
    path = write_manifest(
        tmp_path,
        valid_manifest().replace(
            'profile = "correctness_only"',
            'profile = "steady_state"\nrepetitions = 999\nreport_percentiles = ["p99"]',
        ),
    )

    with pytest.raises(ValueError, match="p99.*1,000"):
        ExperimentManifest.from_toml(path)


def test_manifest_rejects_steady_state_worker_recycling(tmp_path: Path) -> None:
    """Changing a steady-state backend to recycle workers must fail."""
    path = write_manifest(
        tmp_path,
        valid_manifest().replace(
            "workers = 1",
            "workers = 1\nrecycle_workers = true",
        ).replace(
            'profile = "correctness_only"',
            'profile = "steady_state"\nrepetitions = 1000',
        ),
    )

    with pytest.raises(ValueError, match="steady_state.*recycle"):
        ExperimentManifest.from_toml(path)


def test_manifest_rejects_duplicate_experiment_ids(tmp_path: Path) -> None:
    """Changing a second experiment to reuse an identifier must fail."""
    path = write_manifest(
        tmp_path,
        valid_manifest()
        + '''\

[[experiments]]
id = "echo-smoke"
workload = "echo"
backend = "pyroxide"
profile = "correctness_only"
random_seed = 1729
''',
    )

    with pytest.raises(ValueError, match="duplicate experiment id"):
        ExperimentManifest.from_toml(path)


def test_manifest_requires_a_random_seed(tmp_path: Path) -> None:
    """Removing an experiment's random seed must fail instead of becoming nondeterministic."""
    path = write_manifest(tmp_path, valid_manifest().replace("random_seed = 1729\n", ""))

    with pytest.raises(ValueError, match="random_seed"):
        ExperimentManifest.from_toml(path)


def test_manifest_rejects_unknown_keys(tmp_path: Path) -> None:
    """Adding a misspelled manifest option must fail rather than be ignored."""
    path = write_manifest(tmp_path, valid_manifest() + "\nmisspelled_option = true\n")

    with pytest.raises(ValueError, match="unknown keys"):
        ExperimentManifest.from_toml(path)


def test_trial_observation_json_is_versioned_and_uses_unit_names() -> None:
    """Removing schema or unit suffixes from emitted JSON must fail this contract."""
    observation = TrialObservation(
        experiment_id="echo-smoke",
        trial_index=0,
        latency_samples_seconds=(0.001, 0.002),
        resource_samples=(ResourceSample(rss_bytes=4096, cpu_time_seconds=0.01),),
    )

    rendered = json.loads(observation.to_json())

    assert rendered == {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": "echo-smoke",
        "trial_index": 0,
        "latency_samples_seconds": [0.001, 0.002],
        "resource_samples": [{"rss_bytes": 4096, "cpu_time_seconds": 0.01}],
    }


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_trial_observation_rejects_non_finite_latency_samples(
    non_finite: float,
) -> None:
    """Changing a latency sample to a non-finite value must fail strict JSON output."""
    with pytest.raises(ValueError, match="latency_samples_seconds.*finite"):
        TrialObservation(
            experiment_id="echo-smoke",
            trial_index=0,
            latency_samples_seconds=(non_finite,),
            resource_samples=(),
        )


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_resource_sample_rejects_non_finite_cpu_seconds(non_finite: float) -> None:
    """Changing CPU seconds to a non-finite value must fail strict JSON output."""
    with pytest.raises(ValueError, match="cpu_time_seconds.*finite"):
        ResourceSample(rss_bytes=4096, cpu_time_seconds=non_finite)
