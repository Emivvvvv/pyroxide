"""Validate broker study JSONL and render descriptive summaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_BACKENDS = ("celery_redis", "dramatiq_redis")


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize(paths: Sequence[Path]) -> dict[str, Any]:
    """Validate complete paired blocks and return descriptive statistics."""
    rows: list[dict[str, Any]] = []
    sources = []
    for path in paths:
        content = path.read_bytes()
        sources.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        rows.extend(
            json.loads(line) for line in content.decode("utf-8").splitlines() if line
        )
    if not rows:
        raise ValueError("broker report requires at least one observation")

    by_workload: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("status") != "ok":
            raise ValueError("broker report rejects error observations")
        if row.get("comparison_scope") != "broker_operational_separate":
            raise ValueError("broker observation has the wrong comparison scope")
        if row.get("correct_results") != row.get("tasks"):
            raise ValueError("broker observation did not verify every result")
        by_workload[str(row["workload"])].append(row)

    groups = []
    comparisons = []
    for workload, workload_rows in sorted(by_workload.items()):
        block_counts = {int(row["study_blocks"]) for row in workload_rows}
        if len(block_counts) != 1:
            raise ValueError("inconsistent study block count")
        blocks = block_counts.pop()
        indexed: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in workload_rows:
            indexed[int(row["block_index"])][str(row["backend"])] = row
        if set(indexed) != set(range(blocks)):
            raise ValueError("broker study has missing or duplicate blocks")
        for block_index, block in indexed.items():
            if set(block) != set(_BACKENDS):
                raise ValueError("broker study block is not a matched backend pair")
            expected_first = _BACKENDS[block_index % 2]
            if block[expected_first]["order_index"] != 0:
                raise ValueError("broker study order did not alternate")
            if len({row["random_seed"] for row in block.values()}) != 1:
                raise ValueError("matched broker observations used different seeds")

        for backend in _BACKENDS:
            backend_rows = [row for row in workload_rows if row["backend"] == backend]
            throughput = [
                float(row["throughput_tasks_per_second"]) for row in backend_rows
            ]
            makespan = [
                float(row["batch_makespan_seconds"]) for row in backend_rows
            ]
            groups.append(
                {
                    "workload": workload,
                    "backend": backend,
                    "blocks": blocks,
                    "tasks_per_observation": int(backend_rows[0]["tasks"]),
                    "payload_bytes": int(backend_rows[0]["payload_bytes"]),
                    "median_throughput_tasks_per_second": statistics.median(throughput),
                    "p05_throughput_tasks_per_second": _percentile(throughput, 0.05),
                    "p95_throughput_tasks_per_second": _percentile(throughput, 0.95),
                    "median_batch_makespan_seconds": statistics.median(makespan),
                }
            )
        ratios = [
            float(block["celery_redis"]["throughput_tasks_per_second"])
            / float(block["dramatiq_redis"]["throughput_tasks_per_second"])
            for block in indexed.values()
        ]
        comparisons.append(
            {
                "workload": workload,
                "numerator": "celery_redis",
                "denominator": "dramatiq_redis",
                "blocks": blocks,
                "median_throughput_ratio": statistics.median(ratios),
            }
        )

    return {
        "schema_version": 1,
        "comparison_scope": "broker_operational_separate",
        "sources": sources,
        "observations": len(rows),
        "groups": groups,
        "paired_comparisons": comparisons,
    }


def markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Broker operational benchmark",
        "",
        "Separate durable-queue track; do not rank with local executors.",
        "",
        "| Workload | Backend | Blocks | Median tasks/s | p05 | p95 | Median batch s |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group in summary["groups"]:
        lines.append(
            f"| {group['workload']} | {group['backend']} | {group['blocks']} | "
            f"{group['median_throughput_tasks_per_second']:.3f} | "
            f"{group['p05_throughput_tasks_per_second']:.3f} | "
            f"{group['p95_throughput_tasks_per_second']:.3f} | "
            f"{group['median_batch_makespan_seconds']:.6f} |"
        )
    lines.extend(
        [
            "",
            "Paired ratios use the same block seed and alternate backend order.",
            "",
        ]
    )
    for comparison in summary["paired_comparisons"]:
        lines.append(
            f"- {comparison['workload']}: median Celery/Dramatiq throughput ratio "
            f"{comparison['median_throughput_ratio']:.3f}× "
            f"across {comparison['blocks']} blocks."
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = summarize(args.raw)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown(summary), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
