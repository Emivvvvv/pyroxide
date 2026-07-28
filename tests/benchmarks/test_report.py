"""Fixture-only contract tests for benchmark statistical reporting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.benchmarks import report

FIXTURES = Path(__file__).with_name("fixtures")


def test_statistics_use_hand_checked_order_statistics() -> None:
    """Replacing nearest-rank or robust spread calculations must change this result."""
    values = (1.0, 2.0, 3.0, 4.0)

    assert report.median(values) == 2.5
    assert report.nearest_rank(values, 0.25) == 1.0
    assert report.nearest_rank(values, 0.50) == 2.0
    assert report.nearest_rank(values, 0.95) == 4.0
    assert report.iqr(values) == 2.0
    assert report.mad(values) == 1.0


def test_artifact_checksum_is_stable_and_canonical() -> None:
    assert report.artifact_checksum({"b": "2", "a": "1"}) == report.artifact_checksum(
        {"a": "1", "b": "2"}
    )


def test_bootstrap_ci_is_seeded_and_uses_ten_thousand_resamples() -> None:
    """Changing the seed, resample count, or percentile selection must fail."""
    values = (1.0, 2.0, 3.0, 7.0, 9.0)

    assert report.bootstrap_median_ci(values) == (1.0, 9.0)


def test_valid_fixture_reports_paired_ratios_and_required_fields() -> None:
    """Dropping block pairing or any required disclosure must fail this report contract."""
    summary = report.build_summary(FIXTURES / "valid.jsonl")

    assert summary["experiment"] == {
        "id": "fixture-echo",
        "workload": "payload_echo",
        "environment": "fixture-linux",
        "semantics": "steady_state",
    }
    assert summary["cells"]["alpha"]["sample_count"] == 4
    assert summary["cells"]["alpha"]["median_seconds"] == 2.5
    assert summary["cells"]["alpha"]["iqr_seconds"] == 2.0
    assert summary["cells"]["alpha"]["mad_seconds"] == 1.0
    assert summary["cells"]["alpha"]["p95_seconds"] == 4.0
    assert summary["pairs"] == [
        {
            "baseline": "alpha",
            "candidate": "beta",
            "complete_pairs": 4,
            "median_ratio": 2.0,
        }
    ]


def test_cli_writes_neutral_json_and_markdown_from_static_fixture(
    tmp_path: Path,
) -> None:
    """Removing output fields or adding winner language must fail this boundary test."""
    json_path = tmp_path / "summary.json"
    markdown_path = tmp_path / "report.md"

    exit_code = report.main(
        [
            str(FIXTURES / "valid.jsonl"),
            "--json",
            str(json_path),
            "--markdown",
            str(markdown_path),
        ]
    )

    rendered = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8").lower()
    assert exit_code == 0
    assert rendered["cells"]["beta"]["peak_process_tree_rss_bytes"] == 4096
    assert "workload" in markdown
    assert "environment" in markdown
    assert "workers" in markdown
    assert "semantics" in markdown
    assert "sample count" in markdown
    assert "median" in markdown
    assert "uncertainty" in markdown
    assert "throughput" in markdown
    assert "peak process-tree rss" in markdown
    assert "error count" in markdown
    assert "winner" not in markdown


def test_rejects_mismatched_experiment_semantics_before_statistics() -> None:
    """Summarizing records with different semantics under one experiment must fail."""
    with pytest.raises(report.ReportValidationError, match="experiment identity"):
        report.build_summary(FIXTURES / "invalid.jsonl")


def test_rejects_insufficient_complete_samples_before_statistics(tmp_path: Path) -> None:
    """Reducing both paired cells below three observations must refuse a summary."""
    source_lines = (FIXTURES / "valid.jsonl").read_text(encoding="utf-8").splitlines()
    path = tmp_path / "two-pairs.jsonl"
    path.write_text("\n".join(source_lines[:4]) + "\n", encoding="utf-8")

    with pytest.raises(report.ReportValidationError, match="insufficient complete samples"):
        report.build_summary(path)


def test_error_and_missing_cells_remain_explicit_in_summary(tmp_path: Path) -> None:
    """Discarding an error cell to manufacture a paired comparison must fail."""
    rows = [json.loads(line) for line in (FIXTURES / "valid.jsonl").read_text(encoding="utf-8").splitlines()]
    rows[-1]["status"] = "error"
    rows[-1]["error"] = "fixture failure"
    rows[-1].pop("latency_seconds")
    rows[-1].pop("throughput_tasks_per_second")
    rows[-1].pop("peak_process_tree_rss_bytes")
    path = tmp_path / "error.jsonl"
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")

    summary = report.build_summary(path)

    assert summary["cells"]["beta"]["error_count"] == 1
    assert summary["cells"]["beta"]["incomplete"] is True
    assert summary["pairs"] == []


def test_multi_experiment_raw_file_is_grouped_without_cross_comparison(
    tmp_path: Path,
) -> None:
    """Different workloads must never be merged into one ranking table."""
    rows = [
        json.loads(line)
        for line in (FIXTURES / "valid.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    second = []
    for row in rows:
        copy = dict(row)
        copy["run_id"] = "second-" + copy["run_id"]
        copy["experiment_id"] = "fixture-second"
        copy["workload"] = "python_cpu"
        second.append(copy)
    path = tmp_path / "multi.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows + second) + "\n",
        encoding="utf-8",
    )

    summaries = report.build_summaries(path)

    assert [summary["experiment"]["id"] for summary in summaries] == [
        "fixture-echo",
        "fixture-second",
    ]
