"""Collect balanced repeated observations for the separate broker track."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from examples.benchmarks.runner import append_jsonl_event

from .benchmark import BrokerClient, _client, run_batch

_BACKENDS = ("celery", "dramatiq")


def run_study(
    client_factory: Callable[[str], BrokerClient],
    *,
    output: Path,
    blocks: int,
    workload: str,
    payload_bytes: int,
    tasks: int,
    random_seed: int,
    timeout_seconds: float,
) -> int:
    """Run matched blocks, alternating which backend executes first."""
    if blocks <= 0:
        raise ValueError("blocks must be positive")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.touch(exist_ok=False)
    errors = 0
    for block_index in range(blocks):
        order = _BACKENDS if block_index % 2 == 0 else tuple(reversed(_BACKENDS))
        for order_index, backend in enumerate(order):
            try:
                observation = run_batch(
                    client_factory(backend),
                    workload=workload,
                    payload_bytes=payload_bytes,
                    tasks=tasks,
                    random_seed=random_seed + block_index,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as error:
                errors += 1
                observation = {
                    "schema_version": 1,
                    "comparison_scope": "broker_operational_separate",
                    "backend": backend,
                    "workload": workload,
                    "tasks": tasks,
                    "payload_bytes": payload_bytes,
                    "random_seed": random_seed + block_index,
                    "status": "error",
                    "error": str(error),
                }
            observation.update(
                {
                    "block_index": block_index,
                    "order_index": order_index,
                    "study_blocks": blocks,
                }
            )
            append_jsonl_event(output, observation)
            print(json.dumps(observation, sort_keys=True))
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocks", type=int, default=30)
    parser.add_argument("--workload", default="payload_echo")
    parser.add_argument("--payload-bytes", type=int, default=1_024)
    parser.add_argument("--tasks", type=int, default=1_000)
    parser.add_argument("--random-seed", type=int, default=1729)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    return 1 if run_study(
        _client,
        output=args.output,
        blocks=args.blocks,
        workload=args.workload,
        payload_bytes=args.payload_bytes,
        tasks=args.tasks,
        random_seed=args.random_seed,
        timeout_seconds=args.timeout_seconds,
    ) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
