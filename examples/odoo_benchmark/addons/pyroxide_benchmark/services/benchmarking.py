"""Pure measurement helpers for the Odoo ledger-audit example."""

from __future__ import annotations

import json
import math
import statistics
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


def measure_matched_batches(
    payloads: Sequence[bytes],
    *,
    inline: Callable[[bytes], bytes],
    process_batch: Callable[[Sequence[bytes]], Sequence[bytes]],
    isolated_batch: Callable[[Sequence[bytes]], Sequence[bytes]],
    repetitions: int,
    warmups: int,
) -> dict[str, Any]:
    """Measure matched complete batches and verify every isolated result."""
    if not payloads:
        raise ValueError("payloads must not be empty")
    if repetitions <= 0 or warmups < 0:
        raise ValueError("repetitions must be positive and warmups non-negative")
    expected = tuple(inline(payload) for payload in payloads)
    batch_implementations = {
        "process_pool": process_batch,
        "pyroxide_isolated": isolated_batch,
    }
    for _ in range(warmups):
        for name, implementation in batch_implementations.items():
            if tuple(implementation(payloads)) != expected:
                raise RuntimeError(f"{name} result differs from inline oracle")

    samples = {
        "inline_python_seconds": [],
        "process_pool_seconds": [],
        "pyroxide_isolated_seconds": [],
    }
    implementation_order = ("inline", "process_pool", "pyroxide_isolated")
    for block in range(repetitions):
        offset = block % len(implementation_order)
        order = implementation_order[offset:] + implementation_order[:offset]
        for implementation in order:
            started = time.perf_counter_ns()
            if implementation == "inline":
                observed = tuple(inline(payload) for payload in payloads)
                key = "inline_python_seconds"
            else:
                observed = tuple(batch_implementations[implementation](payloads))
                key = f"{implementation}_seconds"
            elapsed = (time.perf_counter_ns() - started) / 1_000_000_000
            if observed != expected:
                raise RuntimeError(f"{implementation} result differs from inline oracle")
            samples[key].append(elapsed)

    return {
        "schema_version": 1,
        "semantics": "matched_batch_makespan",
        "tasks_per_batch": len(payloads),
        "repetitions": repetitions,
        "warmups": warmups,
        "samples": samples,
        "summary": {
            "inline_python": _summary(samples["inline_python_seconds"], len(payloads)),
            "process_pool": _summary(samples["process_pool_seconds"], len(payloads)),
            "pyroxide_isolated": _summary(
                samples["pyroxide_isolated_seconds"], len(payloads)
            ),
        },
    }


def write_result(path: str | Path, result: dict[str, Any]) -> None:
    """Write one result atomically enough to refuse accidental replacement."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    except FileExistsError as error:
        raise FileExistsError(f"refusing to overwrite benchmark result: {output}") from error


def _summary(samples: Sequence[float], tasks: int) -> dict[str, float]:
    ordered = sorted(samples)
    p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
    median = float(statistics.median(ordered))
    return {
        "median_seconds": median,
        "p95_seconds": p95,
        "median_throughput_tasks_per_second": tasks / median,
    }
