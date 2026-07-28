"""Dramatiq configuration for the approval-gated Redis broker track."""

from __future__ import annotations

import os
from typing import Any, Mapping

from .adapter import TASK_NAME, execute_task_message

DRAMATIQ_VERSION = "2.2.0"
DRAMATIQ_CONFIGURATION = {
    "acknowledgement": "late",
    "broker_url": "redis://127.0.0.1:6379/2",
    "concurrency": 2,
    "max_retries": 3,
    "min_backoff": 1_000,
    "prefetch": 1,
    "result_backend": "redis://127.0.0.1:6379/3",
    "serialization": "json-base64-bytes",
}


def configuration(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Resolve distinct Dramatiq transport/result URLs for worker or host."""
    values = dict(DRAMATIQ_CONFIGURATION)
    environment = os.environ if environ is None else environ
    values["broker_url"] = environment.get(
        "DRAMATIQ_BROKER_URL",
        environment.get("BROKER_URL", values["broker_url"]),
    )
    values["result_backend"] = environment.get(
        "DRAMATIQ_RESULT_BACKEND",
        environment.get("RESULT_BACKEND", values["result_backend"]),
    )
    return values


def common_workload_task(message: Mapping[str, str]) -> dict[str, str]:
    """Approved-worker entry point using the shared bytes-and-checksum contract."""
    return execute_task_message(message)


def register_tasks(actor_registry: Any) -> None:
    """Register the task against a supplied registry without creating a worker."""
    actor_registry.actor(
        actor_name=TASK_NAME,
        max_retries=DRAMATIQ_CONFIGURATION["max_retries"],
        min_backoff=DRAMATIQ_CONFIGURATION["min_backoff"],
    )(common_workload_task)


def create_broker() -> Any:
    """Create a configured Dramatiq Redis broker only in an approved runtime."""
    from dramatiq.brokers.redis import RedisBroker

    return RedisBroker(url=configuration()["broker_url"])


def register_runtime(
    dramatiq: Any,
    *,
    broker: Any,
    result_backend: Any,
    results_middleware: Any,
) -> tuple[Any, Any, Any]:
    """Install the broker, results middleware, and shared result-storing actor."""
    dramatiq.set_broker(broker)
    broker.add_middleware(results_middleware)
    actor = dramatiq.actor(
        actor_name=TASK_NAME,
        max_retries=DRAMATIQ_CONFIGURATION["max_retries"],
        min_backoff=DRAMATIQ_CONFIGURATION["min_backoff"],
        store_results=True,
    )(common_workload_task)
    return broker, result_backend, actor


def create_runtime() -> tuple[Any, Any, Any]:
    """Create matching producer/worker runtime objects without starting a worker."""
    import dramatiq
    from dramatiq.brokers.redis import RedisBroker
    from dramatiq.results import Results
    from dramatiq.results.backends import RedisBackend

    values = configuration()
    broker = RedisBroker(url=values["broker_url"])
    result_backend = RedisBackend(url=values["result_backend"])
    return register_runtime(
        dramatiq,
        broker=broker,
        result_backend=result_backend,
        results_middleware=Results(backend=result_backend),
    )
