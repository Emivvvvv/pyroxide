"""Validate benchmark JSONL observations and render descriptive reports.

The input contract is deliberately self-contained: each trial record carries the
versioned experiment identity and artifact evidence needed to validate a raw
file without consulting a runner or re-executing any benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 1
MIN_COMPLETE_SAMPLES = 3
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 1729

_IDENTITY_FIELDS = ("experiment_id", "workload", "environment", "semantics")
_COMMON_FIELDS = {
    "schema_version",
    "run_id",
    "experiment_id",
    "workload",
    "environment",
    "semantics",
    "artifact_hashes",
    "artifact_checksum",
    "backend",
    "block_index",
    "workers",
    "status",
}
_METRIC_FIELDS = {
    "latency_seconds",
    "throughput_tasks_per_second",
    "peak_process_tree_rss_bytes",
}


class ReportValidationError(ValueError):
    """The raw input cannot safely support a descriptive statistical report."""


def artifact_checksum(hashes: dict[str, str]) -> str:
    """Return the canonical checksum used to bind a record to its artifacts."""
    return hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def median(values: Sequence[float]) -> float:
    """Return the conventional median for one non-empty sample."""
    _require_samples(values)
    return float(statistics.median(values))


def nearest_rank(values: Sequence[float], percentile: float) -> float:
    """Return a nearest-rank percentile using a one-indexed rank."""
    _require_samples(values)
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in (0, 1]")
    ordered = sorted(values)
    return float(ordered[math.ceil(len(ordered) * percentile) - 1])


def iqr(values: Sequence[float]) -> float:
    """Return the nearest-rank interquartile range."""
    return nearest_rank(values, 0.75) - nearest_rank(values, 0.25)


def mad(values: Sequence[float]) -> float:
    """Return the median absolute deviation about the sample median."""
    center = median(values)
    return median(tuple(abs(value - center) for value in values))


def bootstrap_median_ci(values: Sequence[float]) -> tuple[float, float]:
    """Return a deterministic 95% percentile bootstrap interval for the median."""
    _require_samples(values)
    generator = random.Random(BOOTSTRAP_SEED)
    resamples = [
        median(generator.choices(values, k=len(values)))
        for _ in range(BOOTSTRAP_RESAMPLES)
    ]
    return nearest_rank(resamples, 0.025), nearest_rank(resamples, 0.975)


def build_summary(raw_path: str | Path) -> dict[str, Any]:
    """Describe a raw file containing exactly one comparison experiment."""
    summaries = build_summaries(raw_path)
    if len(summaries) != 1:
        raise ReportValidationError(
            "raw JSONL contains multiple experiments; use build_summaries"
        )
    return summaries[0]


def build_summaries(raw_path: str | Path) -> list[dict[str, Any]]:
    """Group a raw stream into isolated workload/environment comparisons."""
    records = _load_records(Path(raw_path))
    identities_by_experiment: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
    for record in records:
        identities_by_experiment[record["experiment_id"]].add(
            tuple(record[field] for field in _IDENTITY_FIELDS)
        )
    if any(len(identities) != 1 for identities in identities_by_experiment.values()):
        raise ReportValidationError(
            "experiment identity or semantics differs between records"
        )
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = tuple(record[field] for field in _IDENTITY_FIELDS)
        grouped[key].append(record)
    return [
        _build_summary_records(grouped[key])
        for key in sorted(grouped, key=lambda values: tuple(map(str, values)))
    ]


def _build_summary_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    _validate_experiment(records)
    _validate_worker_parity(records)
    _validate_artifacts(records)

    blocks = {record["block_index"] for record in records}
    by_backend: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_backend[record["backend"]].append(record)

    cells = {
        backend: _cell_summary(backend_records, blocks)
        for backend, backend_records in sorted(by_backend.items())
    }
    _require_sufficient_complete_samples(cells)

    identity = {field.removesuffix("_id"): records[0][field] for field in _IDENTITY_FIELDS}
    identity["id"] = identity.pop("experiment")
    pairs = _paired_ratios(by_backend, cells, blocks)
    return {"experiment": identity, "cells": cells, "pairs": pairs}


def render_markdown(summary: dict[str, Any]) -> str:
    """Render a concise neutral table from a validated summary."""
    experiment = summary["experiment"]
    lines = [
        "# Benchmark report",
        "",
        (
            f"Experiment: `{experiment['id']}`. Descriptive estimates only; "
            "they do not establish a general ordering outside this environment."
        ),
        "",
        "| Workload | Environment | Workers | Semantics | Backend | Sample count | Median (s) | Uncertainty (95% CI, s) | Throughput (tasks/s) | Peak process-tree RSS (bytes) | Error count | Status |",
        "| --- | --- | ---: | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for backend, cell in summary["cells"].items():
        interval = _format_interval(cell.get("median_ci_seconds"))
        status = "incomplete" if cell["incomplete"] else "complete"
        lines.append(
            "| {workload} | {environment} | {workers} | {semantics} | {backend} | "
            "{sample_count} | {median} | {interval} | {throughput} | {rss} | "
            "{errors} | {status} |".format(
                workload=experiment["workload"],
                environment=experiment["environment"],
                workers=cell["workers"],
                semantics=experiment["semantics"],
                backend=backend,
                sample_count=cell["sample_count"],
                median=_format_number(cell.get("median_seconds")),
                interval=interval,
                throughput=_format_number(cell.get("median_throughput_tasks_per_second")),
                rss=_format_number(cell.get("peak_process_tree_rss_bytes")),
                errors=cell["error_count"],
                status=status,
            )
        )
    if summary["pairs"]:
        lines.extend(
            [
                "",
                "## Paired latency ratios",
                "",
                "| Baseline | Candidate | Complete pairs | Median ratio |",
                "| --- | --- | ---: | ---: |",
            ]
        )
        for pair in summary["pairs"]:
            lines.append(
                "| {baseline} | {candidate} | {complete_pairs} | {median_ratio:.6g} |".format(
                    **pair
                )
            )
    else:
        lines.extend(
            [
                "",
                "Paired ratios are omitted because the observed blocks are incomplete or contain errors.",
            ]
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Write JSON and Markdown summaries; never execute a benchmark."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path, metavar="RAW.jsonl")
    parser.add_argument("--json", required=True, type=Path, metavar="SUMMARY.json")
    parser.add_argument("--markdown", required=True, type=Path, metavar="REPORT.md")
    args = parser.parse_args(argv)
    try:
        summaries = build_summaries(args.raw)
    except (OSError, ReportValidationError) as error:
        parser.error(str(error))
    json_payload: Any = summaries[0] if len(summaries) == 1 else {"summaries": summaries}
    markdown = "\n".join(
        render_markdown(summary).rstrip() for summary in summaries
    ) + "\n"
    args.json.write_text(
        json.dumps(json_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(markdown, encoding="utf-8")
    return 0


def _load_records(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ReportValidationError(f"cannot read raw JSONL: {path}") from error
    if not lines:
        raise ReportValidationError("raw JSONL contains no records")

    records = []
    run_ids = set()
    for line_number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ReportValidationError(f"line {line_number}: invalid JSON") from error
        _validate_record(record, line_number)
        run_id = record["run_id"]
        if run_id in run_ids:
            raise ReportValidationError(f"line {line_number}: duplicate run_id {run_id!r}")
        run_ids.add(run_id)
        records.append(record)
    return records


def _validate_record(record: object, line_number: int) -> None:
    if not isinstance(record, dict):
        raise ReportValidationError(f"line {line_number}: record must be an object")
    status = record.get("status")
    if status not in {"ok", "error"}:
        raise ReportValidationError(f"line {line_number}: status must be 'ok' or 'error'")
    required = _COMMON_FIELDS | (_METRIC_FIELDS if status == "ok" else {"error"})
    allowed = required if status == "ok" else required | _METRIC_FIELDS
    unknown = sorted(set(record) - allowed)
    missing = sorted(required - set(record))
    if unknown or missing:
        details = []
        if unknown:
            details.append("unknown fields: " + ", ".join(unknown))
        if missing:
            details.append("missing fields: " + ", ".join(missing))
        raise ReportValidationError(f"line {line_number}: " + "; ".join(details))
    if record["schema_version"] != SCHEMA_VERSION:
        raise ReportValidationError(f"line {line_number}: unsupported schema version")
    for field in ("run_id", "experiment_id", "workload", "environment", "semantics", "backend"):
        _require_nonempty_string(record[field], field, line_number)
    _require_positive_int(record["workers"], "workers", line_number)
    _require_nonnegative_int(record["block_index"], "block_index", line_number)
    hashes = record["artifact_hashes"]
    if not isinstance(hashes, dict) or not hashes:
        raise ReportValidationError(f"line {line_number}: artifact_hashes must be a non-empty object")
    for name, digest in hashes.items():
        _require_nonempty_string(name, "artifact hash name", line_number)
        if not isinstance(digest, str) or len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ReportValidationError(f"line {line_number}: invalid SHA-256 artifact hash")
    expected_checksum = artifact_checksum(hashes)
    if record["artifact_checksum"] != expected_checksum:
        raise ReportValidationError(f"line {line_number}: artifact checksum mismatch")
    if status == "error":
        _require_nonempty_string(record["error"], "error", line_number)
        return
    _require_positive_number(record["latency_seconds"], "latency_seconds", line_number)
    _require_nonnegative_number(
        record["throughput_tasks_per_second"], "throughput_tasks_per_second", line_number
    )
    _require_nonnegative_int(
        record["peak_process_tree_rss_bytes"], "peak_process_tree_rss_bytes", line_number
    )


def _validate_experiment(records: Iterable[dict[str, Any]]) -> None:
    records = list(records)
    expected = tuple(records[0][field] for field in _IDENTITY_FIELDS)
    for record in records[1:]:
        observed = tuple(record[field] for field in _IDENTITY_FIELDS)
        if observed != expected:
            raise ReportValidationError("experiment identity or semantics differs between records")


def _validate_worker_parity(records: Iterable[dict[str, Any]]) -> None:
    workers_by_block: dict[int, set[int]] = defaultdict(set)
    backend_by_block: dict[int, set[str]] = defaultdict(set)
    for record in records:
        workers_by_block[record["block_index"]].add(record["workers"])
        if record["backend"] in backend_by_block[record["block_index"]]:
            raise ReportValidationError("duplicate backend cell within a paired block")
        backend_by_block[record["block_index"]].add(record["backend"])
    if any(len(values) != 1 for values in workers_by_block.values()):
        raise ReportValidationError("worker parity differs within a paired block")
    if len(set().union(*workers_by_block.values())) != 1:
        raise ReportValidationError("worker parity differs between paired blocks")


def _validate_artifacts(records: Iterable[dict[str, Any]]) -> None:
    workload_hash: str | None = None
    backend_hashes: dict[str, str] = {}
    for record in records:
        hashes = record["artifact_hashes"]
        current_workload_hash = hashes.get("workload")
        current_backend_hash = hashes.get("backend")
        if current_workload_hash is None or current_backend_hash is None:
            raise ReportValidationError("artifact hashes must include workload and backend")
        if workload_hash is None:
            workload_hash = current_workload_hash
        elif current_workload_hash != workload_hash:
            raise ReportValidationError("workload artifact hash differs between records")
        previous_backend_hash = backend_hashes.setdefault(record["backend"], current_backend_hash)
        if previous_backend_hash != current_backend_hash:
            raise ReportValidationError("backend artifact hash differs within a cell")


def _cell_summary(records: Sequence[dict[str, Any]], all_blocks: set[int]) -> dict[str, Any]:
    successes = [record for record in records if record["status"] == "ok"]
    errors = [record for record in records if record["status"] == "error"]
    observed_blocks = {record["block_index"] for record in records}
    workers = records[0]["workers"]
    cell: dict[str, Any] = {
        "workers": workers,
        "sample_count": len(successes),
        "error_count": len(errors),
        "incomplete": bool(errors) or observed_blocks != all_blocks,
    }
    if not successes:
        return cell
    latencies = tuple(record["latency_seconds"] for record in successes)
    throughputs = tuple(record["throughput_tasks_per_second"] for record in successes)
    cell.update(
        {
            "median_seconds": median(latencies),
            "iqr_seconds": iqr(latencies),
            "mad_seconds": mad(latencies),
            "p95_seconds": nearest_rank(latencies, 0.95),
            "median_ci_seconds": list(bootstrap_median_ci(latencies)),
            "median_throughput_tasks_per_second": median(throughputs),
            "peak_process_tree_rss_bytes": max(
                record["peak_process_tree_rss_bytes"] for record in successes
            ),
        }
    )
    return cell


def _require_sufficient_complete_samples(cells: dict[str, dict[str, Any]]) -> None:
    insufficient = [
        backend
        for backend, cell in cells.items()
        if cell["sample_count"] < MIN_COMPLETE_SAMPLES
    ]
    if insufficient:
        raise ReportValidationError(
            "insufficient complete samples for: " + ", ".join(insufficient)
        )


def _paired_ratios(
    by_backend: dict[str, list[dict[str, Any]]],
    cells: dict[str, dict[str, Any]],
    all_blocks: set[int],
) -> list[dict[str, Any]]:
    if any(cell["incomplete"] for cell in cells.values()):
        return []
    complete_by_backend = {
        backend: {record["block_index"]: record for record in records}
        for backend, records in by_backend.items()
    }
    if any(set(records) != all_blocks for records in complete_by_backend.values()):
        return []
    backends = sorted(complete_by_backend)
    if len(backends) < 2:
        return []
    baseline = backends[0]
    result = []
    for candidate in backends[1:]:
        ratios = tuple(
            complete_by_backend[candidate][block]["latency_seconds"]
            / complete_by_backend[baseline][block]["latency_seconds"]
            for block in sorted(all_blocks)
        )
        result.append(
            {
                "baseline": baseline,
                "candidate": candidate,
                "complete_pairs": len(ratios),
                "median_ratio": median(ratios),
            }
        )
    return result


def _require_samples(values: Sequence[float]) -> None:
    if not values:
        raise ValueError("at least one value is required")


def _require_nonempty_string(value: object, field: str, line_number: int) -> None:
    if not isinstance(value, str) or not value:
        raise ReportValidationError(f"line {line_number}: {field} must be a non-empty string")


def _require_positive_int(value: object, field: str, line_number: int) -> None:
    _require_nonnegative_int(value, field, line_number)
    if value <= 0:
        raise ReportValidationError(f"line {line_number}: {field} must be positive")


def _require_nonnegative_int(value: object, field: str, line_number: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReportValidationError(f"line {line_number}: {field} must be a non-negative integer")


def _require_positive_number(value: object, field: str, line_number: int) -> None:
    _require_nonnegative_number(value, field, line_number)
    if value <= 0:
        raise ReportValidationError(f"line {line_number}: {field} must be positive")


def _require_nonnegative_number(value: object, field: str, line_number: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ReportValidationError(f"line {line_number}: {field} must be a finite non-negative number")


def _format_number(value: Any) -> str:
    return "—" if value is None else f"{value:.6g}"


def _format_interval(value: list[float] | None) -> str:
    return "—" if value is None else f"[{value[0]:.6g}, {value[1]:.6g}]"


if __name__ == "__main__":  # pragma: no cover - command-line entry point.
    raise SystemExit(main())
