"""Celery configuration for the approval-gated Redis broker track."""

from __future__ import annotations

import os
from typing import Any, Mapping

from .adapter import TASK_NAME, execute_task_message

CELERY_VERSION = "5.6.3"
CELERY_CONFIGURATION = {
    "accept_content": ["json"],
    "broker_url": "redis://127.0.0.1:6379/0",
    "result_backend": "redis://127.0.0.1:6379/1",
    "result_serializer": "json",
    "task_acks_late": True,
    "task_acks_on_failure_or_timeout": False,
    "task_default_retry_delay": 1,
    "task_max_retries": 3,
    "task_publish_retry": True,
    "task_publish_retry_policy": {"interval_start": 0, "interval_step": 1, "max_retries": 3},
    "task_serializer": "json",
    "task_track_started": True,
    "worker_concurrency": 2,
    "worker_prefetch_multiplier": 1,
}


def configuration(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Resolve producer/worker URLs while preserving all matched settings."""
    values = dict(CELERY_CONFIGURATION)
    environment = os.environ if environ is None else environ
    values["broker_url"] = environment.get(
        "CELERY_BROKER_URL",
        environment.get("BROKER_URL", values["broker_url"]),
    )
    values["result_backend"] = environment.get(
        "CELERY_RESULT_BACKEND",
        environment.get("RESULT_BACKEND", values["result_backend"]),
    )
    return values


def common_workload_task(message: Mapping[str, str]) -> dict[str, str]:
    """Approved-worker entry point using the shared bytes-and-checksum contract."""
    return execute_task_message(message)


def register_tasks(app: Any) -> None:
    """Register the task against a supplied app without creating a worker."""
    app.task(
        name=TASK_NAME,
        serializer="json",
        acks_late=True,
        autoretry_for=(Exception,),
        retry_backoff=True,
        max_retries=3,
    )(common_workload_task)


def create_app() -> Any:
    """Create a configured Celery app only when an approved runtime calls this."""
    from celery import Celery

    values = configuration()
    app = Celery("pyroxide_benchmark", broker=values["broker_url"])
    app.conf.update(values)
    register_tasks(app)
    return app
