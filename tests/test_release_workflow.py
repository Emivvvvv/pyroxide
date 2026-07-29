import copy
import re
from pathlib import Path

import pytest

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


EXPECTED_SOURCE_BOOTSTRAP_ACTIONS = (
    "actions/checkout",
    "actions/setup-python",
    "astral-sh/setup-uv",
    "dtolnay/rust-toolchain",
)


def _load_release_workflow():
    return yaml.safe_load(
        Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    )


def _assert_source_bootstrap_actions_are_pinned(source):
    action_refs = [
        step["uses"]
        for step in source["steps"]
        if isinstance(step, dict) and "uses" in step
    ]
    for identity, action_ref in zip(
        EXPECTED_SOURCE_BOOTSTRAP_ACTIONS, action_refs, strict=True
    ):
        assert re.fullmatch(
            rf"{re.escape(identity)}@[0-9a-fA-F]{{40}}",
            action_ref,
        )


@pytest.mark.skipif(yaml is None, reason="pyyaml is required")
def test_release_workflow_verifies_source_before_building_or_publishing():
    """Release artifacts and publishing wait for the tagged source checks."""
    workflow = _load_release_workflow()

    def as_list(value):
        return [value] if isinstance(value, str) else value

    jobs = workflow["jobs"]
    source = jobs["source-verification"]
    _assert_source_bootstrap_actions_are_pinned(source)
    commands = "\n".join(
        step.get("run", "") for step in source["steps"] if isinstance(step, dict)
    )
    assert any(
        step.get("with", {}).get("python-version") == "3.10"
        for step in source["steps"]
    )
    assert any(
        step.get("with", {}).get("toolchain") == "1.86.0"
        for step in source["steps"]
    )
    assert "uv sync --extra dev" in commands
    assert "uv run maturin develop" in commands
    assert "uv run python -m pytest -q" in commands
    assert "cargo test --no-default-features --all-targets --quiet" in commands

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


@pytest.mark.skipif(yaml is None, reason="pyyaml is required")
def test_source_verification_rejects_a_mutable_bootstrap_action_ref():
    """A branch or version tag must not pass the release bootstrap source gate."""
    workflow = copy.deepcopy(_load_release_workflow())
    source = workflow["jobs"]["source-verification"]
    source["steps"][0]["uses"] = "actions/checkout@main"

    with pytest.raises(AssertionError):
        _assert_source_bootstrap_actions_are_pinned(source)
