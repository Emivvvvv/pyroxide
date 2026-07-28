"""Static-only tests for reproducible Odoo environment planning."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from examples.odoo_benchmark import container_entrypoint, fixture, runner

ROOT = Path(__file__).parents[2]


def test_fixture_plan_is_seeded_and_has_stable_logical_content_digest() -> None:
    """Changing the fixture seed or record ordering must change this plan contract."""
    first = fixture.build_fixture_plan(seed=1729)
    second = fixture.build_fixture_plan(seed=1729)

    assert first == second
    assert first["seed"] == 1729
    assert first["row_counts"] == {"account_move": 6, "account_move_line": 12, "ir_attachment": 3}
    assert first["logical_digest"] == "42840bc31465da625d2dbcaa441ad9d4254608476012662e1ecc65d07a965e6c"
    assert all("payload_sha256" in attachment for attachment in first["attachments"])


def test_capacity_validation_rejects_more_pyroxide_processes_than_budget() -> None:
    """Allowing an over-budget Odoo worker pool would invalidate capacity evidence."""
    with pytest.raises(runner.ProfileValidationError, match="CPU budget"):
        runner.validate_capacity(odoo_workers=3, pyroxide_pool_size=2, cpu_budget=4)


def test_master_profile_is_always_reported_as_odoo_20_preview() -> None:
    """Renaming master to a stable release label must fail this preparation contract."""
    profile = runner.load_profile(ROOT / "examples/odoo_benchmark/manifests/odoo-master-py314.toml")

    assert profile["profile"]["release_label"] == "Odoo 20 preview"
    assert profile["profile"]["odoo_source"] == "odoo_master"


def test_validate_accepts_the_integrated_addon_tree_hash() -> None:
    """Changing the integrated add-on content without its recorded hash must fail."""
    profile = runner.load_profile(ROOT / "examples/odoo_benchmark/manifests/odoo19-py313.toml")
    versions = runner.load_versions(ROOT / "examples/odoo_benchmark/versions.toml")

    issues = runner.validate_profile(profile, versions)

    assert issues == []


def test_plan_lists_boundaries_resources_and_requires_explicit_execution(capsys) -> None:
    """Implicit execution or an omitted measurement boundary must fail this plan API."""
    exit_code = runner.main(["--profile", "odoo19-py314", "--plan"])
    plan = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert plan["execution"] == "available_explicit_only"
    assert plan["profile"]["release_label"] == "Odoo 19"
    assert plan["profile"]["python_image_reference"] == (
        "python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6"
    )
    assert plan["profile"]["postgres_image_reference"] == (
        "postgres:16@sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20"
    )
    assert plan["resources"]["postgres"] == "PostgreSQL 16"
    assert plan["measurement_boundaries"] == [
        "orm_extraction_and_query_count",
        "compute_only",
        "orm_result_write",
        "end_to_end_shell_invocation",
        "authenticated_http_request",
    ]
    assert plan["planned_measurement_boundaries"] == plan["measurement_boundaries"]
    assert plan["executed_measurement_boundaries"] == {
        "correctness": [],
        "performance": ["compute_only"],
    }
    assert plan["health_evidence"] == [
        "total_threads",
        "child_processes",
        "rss_bytes",
        "open_file_descriptors",
        "worker_recycling",
        "next_request_health",
    ]


def test_execution_commands_use_one_scoped_project_and_allow_listed_service() -> None:
    """An unscoped Compose command could alter another Odoo benchmark environment."""
    profile = runner.load_profile(ROOT / "examples/odoo_benchmark/manifests/odoo19-py314.toml")
    versions = runner.load_versions(ROOT / "examples/odoo_benchmark/versions.toml")

    commands = runner.build_execution_commands(
        profile,
        versions,
        project_name="pyroxide-odoo19-py314-case",
    )

    prefix = (
        "docker",
        "compose",
        "--file",
        str(ROOT / "examples/odoo_benchmark/compose.yml"),
        "--project-name",
        "pyroxide-odoo19-py314-case",
        "--profile",
        "execution",
    )
    assert commands["up"] == (*prefix, "up", "--detach", "--wait", "postgres")
    assert commands["run"] == (*prefix, "run", "--rm", "--build", "odoo19-py314")
    assert commands["down"] == (*prefix, "down", "--volumes", "--remove-orphans")
    assert commands["environment"]["PYROXIDE_ODOO_GIT_SHA"] == (
        "f7d322a7c3d27467f997e63fcb1d9b952373ff2b"
    )
    assert commands["environment"]["PYROXIDE_ODOO_REQUIREMENTS_SHA256"] == (
        "f788198a2368e4f38e6a2bdaee2f35af096bcc23943b6c753a9f0192a2dda9f8"
    )
    assert commands["environment"]["PYROXIDE_ODOO_PYTHON_IMAGE"] == (
        "python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6"
    )
    assert commands["environment"]["PYROXIDE_ODOO_RUST_IMAGE"] == (
        "rust:1.86.0-slim-bookworm@sha256:"
        "57d415bbd61ce11e2d5f73de068103c7bd9f3188dc132c97cef4a8f62989e944"
    )


def test_steady_state_performance_disables_worker_recycling(tmp_path: Path) -> None:
    """A steady-state run must not silently inherit the 100-task recycle default."""
    profile = runner.load_profile(ROOT / "examples/odoo_benchmark/manifests/odoo19-py313.toml")
    versions = runner.load_versions(ROOT / "examples/odoo_benchmark/versions.toml")

    commands = runner.build_execution_commands(
        profile,
        versions,
        project_name="pyroxide-odoo-steady-state",
        mode="performance",
        output_directory=tmp_path,
    )

    assert commands["environment"]["PYROXIDE_MAX_TASKS_PER_WORKER"] == "0"
    assert commands["environment"]["PYROXIDE_ODOO_MODE"] == "performance"
    assert commands["environment"]["PYROXIDE_ODOO_OUTPUT"] == (
        "/workspace/results/odoo19-py313.json"
    )


def test_recycling_performance_uses_distinct_result_and_100_task_limit(
    tmp_path: Path,
) -> None:
    """A recycling run must be explicit and cannot overwrite steady-state evidence."""
    profile = runner.load_profile(ROOT / "examples/odoo_benchmark/manifests/odoo19-py313.toml")
    versions = runner.load_versions(ROOT / "examples/odoo_benchmark/versions.toml")

    commands = runner.build_execution_commands(
        profile,
        versions,
        project_name="pyroxide-odoo-recycling",
        mode="performance_recycling",
        output_directory=tmp_path,
    )

    assert commands["environment"]["PYROXIDE_MAX_TASKS_PER_WORKER"] == "100"
    assert commands["environment"]["PYROXIDE_ODOO_MODE"] == "performance_recycling"
    assert commands["environment"]["PYROXIDE_ODOO_OUTPUT"] == (
        "/workspace/results/odoo19-py313-recycling.json"
    )


def test_execution_rejects_invalid_evidence_before_calling_docker() -> None:
    """A mismatched addon hash must prevent any service from starting."""
    profile = runner.load_profile(ROOT / "examples/odoo_benchmark/manifests/odoo19-py313.toml")
    versions = runner.load_versions(ROOT / "examples/odoo_benchmark/versions.toml")
    versions["addon"]["addon_tree_sha256"] = "0" * 64
    calls: list[tuple[str, ...]] = []

    def record_call(command: tuple[str, ...], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    with pytest.raises(runner.ProfileValidationError, match="addon_tree_sha256"):
        runner.execute_profile(
            profile,
            versions,
            project_name="pyroxide-invalid-evidence",
            command_runner=record_call,
        )

    assert calls == []


def test_execution_always_removes_its_scoped_environment_after_failure() -> None:
    """A failed Odoo check must not leave its database or containers behind."""
    profile = runner.load_profile(ROOT / "examples/odoo_benchmark/manifests/odoo19-py313.toml")
    versions = runner.load_versions(ROOT / "examples/odoo_benchmark/versions.toml")
    commands = runner.build_execution_commands(
        profile,
        versions,
        project_name="pyroxide-cleanup-case",
    )
    calls: list[tuple[str, ...]] = []

    def fail_run(command: tuple[str, ...], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return_code = 9 if command == commands["run"] else 0
        return subprocess.CompletedProcess(command, return_code)

    exit_code = runner.execute_profile(
        profile,
        versions,
        project_name="pyroxide-cleanup-case",
        command_runner=fail_run,
    )

    assert exit_code == 9
    assert calls == [commands["up"], commands["run"], commands["down"]]


def test_container_correctness_command_is_bounded_to_the_benchmark_addon() -> None:
    """Running all Odoo tests or enabling workers would change the validation workload."""
    command = container_entrypoint.build_correctness_command(
        odoo_root=Path("/opt/odoo"),
        addons_root=Path("/workspace/addons"),
    )

    assert command == (
        "python",
        "/opt/odoo/odoo-bin",
        "--database=odoo_benchmark",
        "--db_host=postgres",
        "--db_port=5432",
        "--db_user=odoo",
        "--db_password=odoo",
        "--addons-path=/workspace/addons,/opt/odoo/addons",
        "--init=pyroxide_benchmark",
        "--test-enable",
        "--test-tags=pyroxide_benchmark,-pyroxide_benchmark_performance",
        "--stop-after-init",
        "--workers=0",
        "--without-demo=all",
    )


def test_container_performance_command_selects_only_the_timed_driver() -> None:
    command = container_entrypoint.build_performance_command(
        odoo_root=Path("/opt/odoo"),
        addons_root=Path("/workspace/addons"),
    )

    assert "--test-tags=pyroxide_benchmark_performance" in command
    assert "--workers=0" in command


def test_compose_pins_postgres_digest_and_keeps_services_in_execution_profiles() -> None:
    """A mutable database tag or default-starting service would break reproducibility."""
    compose = (ROOT / "examples/odoo_benchmark/compose.yml").read_text(encoding="utf-8")

    assert "postgres:16@sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20" in compose
    assert compose.count("- execution") == 4
    for variable in (
        "PYROXIDE_MAX_TASKS_PER_WORKER",
        "PYROXIDE_ODOO_GIT_SHA",
        "PYROXIDE_ODOO_MODE",
        "PYROXIDE_ODOO_OUTPUT",
        "PYROXIDE_ODOO_RELEASE_LABEL",
    ):
        assert compose.count(f"{variable}: ${{{variable}:?set by runner.py}}") == 3
