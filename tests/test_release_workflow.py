from pathlib import Path

import pytest


def test_release_workflow_verifies_source_before_building_or_publishing():
    """Release artifacts and publishing wait for the tagged source checks."""
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(
        Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    )

    def as_list(value):
        return [value] if isinstance(value, str) else value

    jobs = workflow["jobs"]
    source = jobs["source-verification"]
    commands = "\n".join(
        step.get("run", "") for step in source["steps"] if isinstance(step, dict)
    )
    assert "uv run python -m pytest -q" in commands
    assert "cargo test --all-targets --quiet" in commands
    for job in ("build-wheels", "build-freethreaded-wheels", "build-sdist"):
        assert set(as_list(jobs[job]["needs"])) >= {
            "validate-release",
            "source-verification",
        }
    assert set(as_list(jobs["publish"]["needs"])) == {
        "validate-release",
        "source-verification",
        "build-wheels",
        "build-freethreaded-wheels",
        "build-sdist",
    }
    assert jobs["publish"]["environment"] == "pypi"
    assert jobs["publish"]["permissions"]["id-token"] == "write"
