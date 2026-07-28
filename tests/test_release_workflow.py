from pathlib import Path

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


def test_release_workflow_verifies_source_before_building_or_publishing():
    """Release artifacts and publishing wait for the tagged source checks."""
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
    assert source["steps"][0]["uses"].startswith("actions/checkout@")
    assert any(
        step.get("with", {}).get("python-version") == "3.10"
        for step in source["steps"]
    )
    assert any(
        step.get("uses", "").startswith("astral-sh/setup-uv@")
        for step in source["steps"]
    )
    assert any(
        step.get("with", {}).get("toolchain") == "1.86.0"
        for step in source["steps"]
    )
    assert "uv sync --extra dev" in commands
    assert "uv run maturin develop" in commands
    assert "uv run python -m pytest -q" in commands
    assert "cargo test --all-targets --quiet" in commands

    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]
    assert "pyyaml>=6,<7" in {
        requirement.lower().replace("_", "-").replace(" ", "")
        for requirement in dev_dependencies
    }
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
