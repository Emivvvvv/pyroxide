"""Availability and dry-run contracts for semantically separate competitors."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from examples.benchmarks import distributed_availability


def test_docker_probe_rejects_a_cli_success_without_server_metadata() -> None:
    """Docker Desktop may exit zero after printing a daemon connection failure."""

    def disconnected(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["docker", "version"],
            returncode=0,
            stdout="null\n",
            stderr="Cannot connect to the Docker daemon",
        )

    assert distributed_availability._docker_status(command_runner=disconnected) == (
        False,
        "Cannot connect to the Docker daemon",
    )


def test_availability_report_never_substitutes_missing_distributed_packages() -> None:
    """A missing Ray/Dask/broker dependency must be recorded, never silently replaced."""
    versions = {
        "celery": "5.6.3",
        "dramatiq": "2.2.0",
        "redis": "6.4.0",
        "distributed": "2026.7.1",
    }

    report = distributed_availability.collect(
        version_lookup=lambda name: versions.get(name),
        executable_lookup=lambda name: f"/usr/bin/{name}" if name == "docker" else None,
        docker_probe=lambda: (False, "daemon unavailable"),
    )

    assert report == {
        "schema_version": 1,
        "comparison_scope": "separate_from_local_executor_tables",
        "components": {
            "celery": {"available": True, "version": "5.6.3"},
            "dask_single_node": {"available": True, "version": "2026.7.1"},
            "dramatiq": {"available": True, "version": "2.2.0"},
            "ray_single_node": {
                "available": False,
                "reason": "package not installed",
                "version": None,
            },
            "redis_client": {"available": True, "version": "6.4.0"},
        },
        "container_runtime": {
            "available": False,
            "executable": "/usr/bin/docker",
            "reason": "daemon unavailable",
        },
        "runnable_tracks": {
            "brokers": False,
            "dask_single_node": True,
            "ray_single_node": False,
        },
    }


def test_availability_rejects_an_installed_but_unpinned_version() -> None:
    """Mixing versions across runs would make the separate comparison irreproducible."""
    report = distributed_availability.collect(
        version_lookup=lambda name: "0.0.0" if name == "ray" else None,
        executable_lookup=lambda name: None,
    )

    assert report["components"]["ray_single_node"] == {
        "available": False,
        "reason": "expected 2.56.1, found 0.0.0",
        "version": "0.0.0",
    }
    assert report["runnable_tracks"]["ray_single_node"] is False


def test_dry_run_prints_commands_without_executing_them(capsys) -> None:
    """A dry run that starts Docker or omits result paths would not be safe or reproducible."""
    exit_code = distributed_availability.main(["--dry-run"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["comparison_scope"] == "separate_from_local_executor_tables"
    assert output["commands"] == {
        "create_host_environment": [
            "uv",
            "venv",
            "--python",
            "3.14",
            ".benchmark-envs/distributed",
        ],
        "install_host_dependencies": [
            "uv",
            "pip",
            "install",
            "--python",
            ".benchmark-envs/distributed/bin/python",
            "-r",
            "examples/benchmarks/requirements-distributed.txt",
        ],
        "broker_services_start": [
            "docker",
            "compose",
            "-f",
            "examples/benchmarks/compose.brokers.yml",
            "--profile",
            "execution",
            "up",
            "-d",
            "--wait",
        ],
        "broker_services_stop": [
            "docker",
            "compose",
            "-f",
            "examples/benchmarks/compose.brokers.yml",
            "--profile",
            "execution",
            "down",
        ],
        "celery_smoke": [
            ".benchmark-envs/distributed/bin/python",
            "-m",
            "examples.benchmarks.brokers.benchmark",
            "--backend",
            "celery",
            "--tasks",
            "10",
            "--output",
            "benchmark_results/distributed/celery-smoke.json",
        ],
        "dramatiq_smoke": [
            ".benchmark-envs/distributed/bin/python",
            "-m",
            "examples.benchmarks.brokers.benchmark",
            "--backend",
            "dramatiq",
            "--tasks",
            "10",
            "--output",
            "benchmark_results/distributed/dramatiq-smoke.json",
        ],
        "local_distributed": [
            ".benchmark-envs/distributed/bin/python",
            "-m",
            "examples.benchmarks.runner",
            "--manifest",
            "examples/benchmarks/manifests/distributed.toml",
            "--output",
            "benchmark_results/distributed/raw.jsonl",
        ],
    }


def test_availability_cli_can_preserve_the_exact_report(
    tmp_path: Path, capsys
) -> None:
    """An availability check used for a run must be persistable without shell rewriting."""
    output_path = tmp_path / "availability.json"

    exit_code = distributed_availability.main(
        ["--dry-run", "--output", str(output_path)]
    )

    assert exit_code == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == json.loads(
        capsys.readouterr().out
    )
