"""Run a bounded broker-backed batch in its separate comparison scope."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from examples.benchmarks.workloads import expected_result

from .adapter import TASK_NAME, TaskMessage


class BrokerClient(Protocol):
    name: str

    def submit(self, message: dict[str, str]) -> Any: ...

    def result(
        self, handle: Any, *, timeout_seconds: float
    ) -> Mapping[str, str]: ...

    def close(self) -> None: ...


def _messages(
    *,
    workload: str,
    payload_bytes: int,
    tasks: int,
    random_seed: int,
) -> tuple[TaskMessage, ...]:
    if payload_bytes < 0:
        raise ValueError("payload_bytes must not be negative")
    if tasks <= 0:
        raise ValueError("tasks must be positive")
    generator = random.Random(random_seed)
    messages: list[TaskMessage] = []
    for _ in range(tasks):
        payload = generator.randbytes(payload_bytes)
        expected = expected_result(workload, payload)
        messages.append(
            TaskMessage.from_bytes(
                workload=workload,
                payload=payload,
                expected_result_checksum=hashlib.sha256(expected).hexdigest(),
            )
        )
    return tuple(messages)


def run_batch(
    client: BrokerClient,
    *,
    workload: str,
    payload_bytes: int,
    tasks: int,
    random_seed: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Submit the complete batch, await it, and verify every returned byte string."""
    messages = _messages(
        workload=workload,
        payload_bytes=payload_bytes,
        tasks=tasks,
        random_seed=random_seed,
    )
    started_ns = time.perf_counter_ns()
    try:
        handles = [client.submit(message.to_wire()) for message in messages]
        results = [
            client.result(handle, timeout_seconds=timeout_seconds)
            for handle in handles
        ]
        for message, result in zip(messages, results, strict=True):
            encoded = result.get("result_base64")
            checksum = result.get("result_checksum")
            if not isinstance(encoded, str) or not isinstance(checksum, str):
                raise ValueError("broker result is missing bytes or checksum")
            try:
                result_bytes = base64.b64decode(encoded, validate=True)
            except ValueError as error:
                raise ValueError("broker result is not valid base64") from error
            actual_checksum = hashlib.sha256(result_bytes).hexdigest()
            if actual_checksum != checksum:
                raise ValueError("result bytes do not match checksum")
            if checksum != message.expected_result_checksum:
                raise ValueError("result checksum does not match workload oracle")
    finally:
        client.close()
    elapsed = (time.perf_counter_ns() - started_ns) / 1_000_000_000
    return {
        "schema_version": 1,
        "comparison_scope": "broker_operational_separate",
        "backend": client.name,
        "workload": workload,
        "tasks": tasks,
        "payload_bytes": payload_bytes,
        "random_seed": random_seed,
        "correct_results": len(results),
        "batch_makespan_seconds": elapsed,
        "throughput_tasks_per_second": tasks / elapsed,
        "status": "ok",
    }


class CeleryClient:
    name = "celery_redis"

    def __init__(self) -> None:
        from .celery_app import create_app

        self._app = create_app()

    def submit(self, message: dict[str, str]) -> Any:
        return self._app.send_task(TASK_NAME, args=[message])

    def result(
        self, handle: Any, *, timeout_seconds: float
    ) -> Mapping[str, str]:
        return handle.get(timeout=timeout_seconds)

    def close(self) -> None:
        self._app.close()


class DramatiqClient:
    name = "dramatiq_redis"

    def __init__(self) -> None:
        from .dramatiq_app import create_runtime

        self._broker, self._result_backend, self._actor = create_runtime()

    def submit(self, message: dict[str, str]) -> Any:
        return self._actor.send(message)

    def result(
        self, handle: Any, *, timeout_seconds: float
    ) -> Mapping[str, str]:
        return handle.get_result(
            backend=self._result_backend,
            block=True,
            timeout=int(timeout_seconds * 1_000),
        )

    def close(self) -> None:
        self._broker.close()


def _client(name: str) -> BrokerClient:
    if name == "celery":
        return CeleryClient()
    if name == "dramatiq":
        return DramatiqClient()
    raise ValueError(f"unsupported broker backend: {name}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("celery", "dramatiq"), required=True)
    parser.add_argument("--workload", default="payload_echo")
    parser.add_argument("--payload-bytes", type=int, default=1_024)
    parser.add_argument("--tasks", type=int, default=10)
    parser.add_argument("--random-seed", type=int, default=1729)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    observation = run_batch(
        _client(args.backend),
        workload=args.workload,
        payload_bytes=args.payload_bytes,
        tasks=args.tasks,
        random_seed=args.random_seed,
        timeout_seconds=args.timeout_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(observation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(observation, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
