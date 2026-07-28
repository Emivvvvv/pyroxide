"""Shared, non-executing broker task and operational-plan contracts."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

BROKER_SETTINGS = {
    "acknowledgement": "late",
    "concurrency": 2,
    "prefetch": 1,
    "result_backend": "redis",
    "retry": {"max_attempts": 3, "policy": "bounded_exponential"},
    "serialization": "json-base64-bytes",
    "transport": "redis",
}
TASK_NAME = "benchmarks.common_workload"
PLAN = {
    "cleanup": [
        "stop the execution profile after approved collection",
        "remove the dedicated benchmark Redis volume",
        "retain exported event files outside the volume before removal",
    ],
    "execution": "explicit_commands_only",
    "ports": [{"host": 6379, "service": "redis"}],
    "resources": {
        "disk": "2 GiB for Redis persistence and logs",
        "memory": "512 MiB Redis; 768 MiB per worker",
    },
    "services": ["redis", "celery-worker", "dramatiq-worker"],
}


@dataclass(frozen=True, slots=True)
class TaskMessage:
    """JSON-safe common-workload request accepted by both broker adapters."""

    workload: str
    payload_base64: str
    expected_result_checksum: str

    @classmethod
    def from_bytes(
        cls, *, workload: str, payload: bytes, expected_result_checksum: str
    ) -> TaskMessage:
        return cls(
            workload=workload,
            payload_base64=base64.b64encode(payload).decode("ascii"),
            expected_result_checksum=expected_result_checksum,
        )

    def payload_bytes(self) -> bytes:
        return base64.b64decode(self.payload_base64, validate=True)

    def to_wire(self) -> dict[str, str]:
        return asdict(self)


def validate_result_bytes(result: bytes, expected_result_checksum: str) -> bool:
    """Check result evidence without executing a workload or contacting a broker."""
    return hashlib.sha256(result).hexdigest() == expected_result_checksum


def execute_task_message(message: Mapping[str, str]) -> dict[str, str]:
    """Execute at an approved worker boundary and return a verified result frame.

    This function is deliberately not reachable from plan mode or static tests.
    """
    task_message = TaskMessage(**message)
    from examples.benchmarks.workloads import run_workload

    result = run_workload(task_message.workload, task_message.payload_bytes())
    if not validate_result_bytes(result, task_message.expected_result_checksum):
        raise ValueError("common workload result checksum mismatch")
    return {
        "result_base64": base64.b64encode(result).decode("ascii"),
        "result_checksum": task_message.expected_result_checksum,
    }


def broker_event(event: str, *, broker: str, message_id: str) -> dict[str, Any]:
    """Describe one enqueue, result, or failure event with durability metadata."""
    if event not in {"enqueue", "result", "failure"}:
        raise ValueError("event must be enqueue, result, or failure")
    if broker not in {"celery", "dramatiq"}:
        raise ValueError("broker must be celery or dramatiq")
    if not message_id:
        raise ValueError("message_id must not be empty")
    return {"event": event, "broker": broker, "message_id": message_id, **BROKER_SETTINGS}


def main(argv: Sequence[str] | None = None) -> int:
    """Print the explicit broker resource plan without starting services."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="Print requirements only")
    args = parser.parse_args(argv)
    if not args.plan:
        parser.error("only --plan is available; use the benchmark module to execute")
    print(json.dumps(PLAN, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - command-line entry point.
    raise SystemExit(main())
