"""Static contracts for the opt-in broker operational track."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import tomllib

from examples.benchmarks.brokers import (
    adapter,
    benchmark,
    celery_app,
    dramatiq_app,
    study,
)
from examples.benchmarks.brokers import report as broker_report

ROOT = Path(__file__).parents[2]


class CeleryRegistrar:
    """Minimal static registration boundary; it never imports or starts Celery."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def task(self, **options: object):
        def decorate(function: object) -> object:
            self.calls.append((function.__name__, options))
            return function

        return decorate


class DramatiqRegistrar:
    """Minimal static registration boundary; it never imports or starts Dramatiq."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def actor(self, **options: object):
        def decorate(function: object) -> object:
            self.calls.append((function.__name__, options))
            return function

        return decorate


class FakeDramatiqRuntime:
    def __init__(self) -> None:
        self.broker = None
        self.actor_options: dict[str, object] | None = None

    def set_broker(self, broker: object) -> None:
        self.broker = broker

    def actor(self, **options: object):
        self.actor_options = options

        def decorate(function: object) -> tuple[str, object]:
            return ("actor", function)

        return decorate


class FakeDramatiqBroker:
    def __init__(self) -> None:
        self.middleware: list[object] = []

    def add_middleware(self, middleware: object) -> None:
        self.middleware.append(middleware)


class RecordingBrokerClient:
    """In-memory transport double; the common workload execution remains real."""

    name = "fixture-broker"

    def __init__(self, *, corrupt_result: bool = False) -> None:
        self.events: list[str] = []
        self.corrupt_result = corrupt_result

    def submit(self, message: dict[str, str]) -> dict[str, str]:
        self.events.append("submit")
        return message

    def result(
        self, handle: dict[str, str], *, timeout_seconds: float
    ) -> dict[str, str]:
        assert timeout_seconds == 5.0
        self.events.append("result")
        result = adapter.execute_task_message(handle)
        if self.corrupt_result:
            result["result_base64"] = "Y29ycnVwdA=="
        return result

    def close(self) -> None:
        self.events.append("close")


def test_message_contract_preserves_payload_and_expected_result_checksum() -> None:
    """Changing payload encoding or checksum validation must reject this envelope."""
    expected_result = b"common-workload-result"
    message = adapter.TaskMessage.from_bytes(
        workload="payload_echo",
        payload=b"\x00fixture",
        expected_result_checksum=hashlib.sha256(expected_result).hexdigest(),
    )

    rendered = message.to_wire()

    assert rendered == {
        "expected_result_checksum": "5e98f352fe7aaad56d60d9680e4daed7cb3c42d257c0cd9a802fd261637ef485",
        "payload_base64": "AGZpeHR1cmU=",
        "workload": "payload_echo",
    }
    assert adapter.validate_result_bytes(expected_result, message.expected_result_checksum)


def test_invalid_result_bytes_are_rejected_without_executing_a_workload() -> None:
    """Accepting a mismatched checksum would make broker result events untrustworthy."""
    checksum = hashlib.sha256(b"expected").hexdigest()

    assert not adapter.validate_result_bytes(b"other", checksum)


def test_broker_batch_submits_before_waiting_and_verifies_every_result() -> None:
    """Serial submit/result pairs or unchecked result bytes would skew broker evidence."""
    client = RecordingBrokerClient()

    observation = benchmark.run_batch(
        client,
        workload="payload_echo",
        payload_bytes=8,
        tasks=3,
        random_seed=1729,
        timeout_seconds=5.0,
    )

    assert client.events == [
        "submit",
        "submit",
        "submit",
        "result",
        "result",
        "result",
        "close",
    ]
    assert observation["schema_version"] == 1
    assert observation["comparison_scope"] == "broker_operational_separate"
    assert observation["backend"] == "fixture-broker"
    assert observation["workload"] == "payload_echo"
    assert observation["tasks"] == 3
    assert observation["payload_bytes"] == 8
    assert observation["correct_results"] == 3
    assert observation["status"] == "ok"
    assert observation["batch_makespan_seconds"] >= 0
    assert observation["throughput_tasks_per_second"] > 0


def test_broker_batch_rejects_a_result_with_correct_checksum_but_wrong_bytes() -> None:
    """Trusting a returned checksum without hashing returned bytes would accept corruption."""
    client = RecordingBrokerClient(corrupt_result=True)

    with pytest.raises(ValueError, match="result bytes do not match checksum"):
        benchmark.run_batch(
            client,
            workload="payload_echo",
            payload_bytes=8,
            tasks=1,
            random_seed=1729,
            timeout_seconds=5.0,
        )

    assert client.events[-1] == "close"


def test_broker_study_rotates_order_and_preserves_each_matched_observation(
    tmp_path: Path,
) -> None:
    """A single batch or fixed backend order would not support a balanced comparison."""
    created: list[str] = []

    def client_factory(name: str) -> RecordingBrokerClient:
        created.append(name)
        client = RecordingBrokerClient()
        client.name = name
        return client

    output = tmp_path / "broker-study.jsonl"
    errors = study.run_study(
        client_factory,
        output=output,
        blocks=2,
        workload="payload_echo",
        payload_bytes=8,
        tasks=2,
        random_seed=1729,
        timeout_seconds=5.0,
    )
    rows = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]

    assert errors == 0
    assert created == ["celery", "dramatiq", "dramatiq", "celery"]
    assert [(row["block_index"], row["order_index"]) for row in rows] == [
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
    ]
    assert [row["random_seed"] for row in rows] == [1729, 1729, 1730, 1730]
    assert all(row["comparison_scope"] == "broker_operational_separate" for row in rows)
    assert all(row["correct_results"] == 2 for row in rows)


def test_broker_report_validates_blocks_and_summarizes_paired_throughput(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw.jsonl"
    rows = []
    for block, (first, second) in enumerate(
        (("celery_redis", "dramatiq_redis"), ("dramatiq_redis", "celery_redis"))
    ):
        for order, backend in enumerate((first, second)):
            throughput = 200.0 if backend == "celery_redis" else 100.0
            rows.append(
                {
                    "schema_version": 1,
                    "comparison_scope": "broker_operational_separate",
                    "backend": backend,
                    "workload": "payload_echo",
                    "tasks": 10,
                    "payload_bytes": 8,
                    "random_seed": 1729 + block,
                    "correct_results": 10,
                    "batch_makespan_seconds": 10 / throughput,
                    "throughput_tasks_per_second": throughput,
                    "status": "ok",
                    "block_index": block,
                    "order_index": order,
                    "study_blocks": 2,
                }
            )
    raw.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    summary = broker_report.summarize((raw,))

    assert [group["backend"] for group in summary["groups"]] == [
        "celery_redis",
        "dramatiq_redis",
    ]
    assert summary["groups"][0]["median_throughput_tasks_per_second"] == 200.0
    assert summary["paired_comparisons"] == [
        {
            "workload": "payload_echo",
            "numerator": "celery_redis",
            "denominator": "dramatiq_redis",
            "blocks": 2,
            "median_throughput_ratio": 2.0,
        }
    ]


def test_broker_apps_register_the_same_common_task_with_recorded_durability() -> None:
    """Renaming either task or omitting acknowledgement/retry settings must fail."""
    celery = CeleryRegistrar()
    dramatiq = DramatiqRegistrar()

    celery_app.register_tasks(celery)
    dramatiq_app.register_tasks(dramatiq)

    assert celery.calls == [
        (
            "common_workload_task",
            {
                "acks_late": True,
                "autoretry_for": (Exception,),
                "max_retries": 3,
                "name": "benchmarks.common_workload",
                "retry_backoff": True,
                "serializer": "json",
            },
        )
    ]
    assert dramatiq.calls == [
        (
            "common_workload_task",
            {
                "actor_name": "benchmarks.common_workload",
                "max_retries": 3,
                "min_backoff": 1_000,
            },
        )
    ]


def test_dramatiq_runtime_registers_results_and_a_result_storing_actor() -> None:
    """Without Results middleware and store_results, an actual producer cannot await work."""
    dramatiq = FakeDramatiqRuntime()
    broker = FakeDramatiqBroker()
    result_backend = object()
    results = object()

    runtime = dramatiq_app.register_runtime(
        dramatiq,
        broker=broker,
        result_backend=result_backend,
        results_middleware=results,
    )

    assert dramatiq.broker is broker
    assert broker.middleware == [results]
    assert dramatiq.actor_options == {
        "actor_name": "benchmarks.common_workload",
        "max_retries": 3,
        "min_backoff": 1_000,
        "store_results": True,
    }
    assert runtime == (broker, result_backend, ("actor", dramatiq_app.common_workload_task))


def test_runtime_urls_are_overridable_and_brokers_use_separate_redis_databases() -> None:
    """Sharing protocol/result databases or ignoring host URLs would invalidate execution."""
    celery = celery_app.configuration(
        {
            "CELERY_BROKER_URL": "redis://host:6379/4",
            "CELERY_RESULT_BACKEND": "redis://host:6379/5",
        }
    )
    dramatiq = dramatiq_app.configuration(
        {
            "DRAMATIQ_BROKER_URL": "redis://host:6379/6",
            "DRAMATIQ_RESULT_BACKEND": "redis://host:6379/7",
        }
    )

    assert celery["broker_url"] == "redis://host:6379/4"
    assert celery["result_backend"] == "redis://host:6379/5"
    assert dramatiq["broker_url"] == "redis://host:6379/6"
    assert dramatiq["result_backend"] == "redis://host:6379/7"
    assert celery_app.configuration({})["broker_url"] == "redis://127.0.0.1:6379/0"
    assert celery_app.configuration({})["result_backend"] == "redis://127.0.0.1:6379/1"
    assert dramatiq_app.configuration({})["broker_url"] == "redis://127.0.0.1:6379/2"
    assert dramatiq_app.configuration({})["result_backend"] == "redis://127.0.0.1:6379/3"


def test_durability_events_include_broker_semantics_without_claiming_equivalence() -> None:
    """Dropping operational metadata or claiming local-executor equivalence must fail."""
    event = adapter.broker_event("enqueue", broker="celery", message_id="fixture-1")

    assert event == {
        "acknowledgement": "late",
        "broker": "celery",
        "concurrency": 2,
        "event": "enqueue",
        "message_id": "fixture-1",
        "prefetch": 1,
        "result_backend": "redis",
        "retry": {"max_attempts": 3, "policy": "bounded_exponential"},
        "serialization": "json-base64-bytes",
        "transport": "redis",
    }


def test_broker_manifest_is_operational_and_not_a_local_executor_ranking_input() -> None:
    """Adding broker cases to local ranking would violate the separate-track boundary."""
    with (ROOT / "examples/benchmarks/manifests/brokers.toml").open("rb") as manifest_file:
        manifest = tomllib.load(manifest_file)

    assert manifest["operational_track"] == {
        "comparison_scope": "not_ranked_with_local_executors",
        "execution": "explicit_commands_only",
        "kind": "broker_backed",
    }
    assert manifest["versions"] == {
        "celery": "5.6.3",
        "dramatiq": "2.2.0",
        "redis_client": "6.4.0",
        "redis_image": (
            "redis:7.4.2-alpine@"
            "sha256:02419de7eddf55aa5bcf49efb74e88fa8d931b4d77c07eff8a6b2144472b6952"
        ),
        "worker_image": (
            "python:3.14.0-slim@"
            "sha256:0aecac02dc3d4c5dbb024b753af084cafe41f5416e02193f1ce345d671ec966e"
        ),
    }
    assert manifest["images"]["resolved_platform"] == "linux/arm64/v8"
    assert manifest["study"] == {
        "blocks": 30,
        "order": "alternating",
        "random_seed": 1729,
        "workloads": [
            {"id": "payload_echo", "tasks": 1000, "payload_bytes": 1024},
            {"id": "python_cpu", "tasks": 100, "payload_bytes": 1024},
        ],
    }
    assert manifest["celery"]["acknowledgement"] == "late"
    assert manifest["dramatiq"]["prefetch"] == 1


def test_compose_configuration_uses_pinned_images_and_execution_profiles_only() -> None:
    """Unpinned images or default-starting workers must fail operational review."""
    compose = (ROOT / "examples/benchmarks/compose.brokers.yml").read_text(encoding="utf-8")

    assert (
        "image: redis:7.4.2-alpine@"
        "sha256:02419de7eddf55aa5bcf49efb74e88fa8d931b4d77c07eff8a6b2144472b6952"
    ) in compose
    assert compose.count(
        "image: python:3.14.0-slim@"
        "sha256:0aecac02dc3d4c5dbb024b753af084cafe41f5416e02193f1ce345d671ec966e"
    ) == 2
    assert compose.count("- execution") == 3
    assert '"127.0.0.1:${BROKER_BENCHMARK_REDIS_PORT:-6379}:6379"' in compose
    assert "mem_limit: 512m" in compose
    assert "mem_limit: 768m" in compose


def test_compose_file_resolves_project_mount_and_worker_commands() -> None:
    """A compose file that mounts only examples/benchmarks cannot import the package."""
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "examples/benchmarks/compose.brokers.yml"),
            "--profile",
            "execution",
            "config",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    rendered = completed.stdout
    assert f"source: {ROOT}" in rendered
    assert rendered.count("target: /workspace") == 2
    assert "BENCHMARK_CELERY_VERSION: 5.6.3" in rendered
    assert "\n      CELERY_VERSION:" not in rendered
    assert "examples.benchmarks.brokers.celery_worker:app" in rendered
    assert "examples.benchmarks.brokers.dramatiq_worker" in rendered
    assert "dramatiq_queue_prefetch: \"1\"" in rendered
    assert "DRAMATIQ_BROKER_URL: redis://redis:6379/2" in rendered
    assert "DRAMATIQ_RESULT_BACKEND: redis://redis:6379/3" in rendered


def test_plan_lists_services_resources_ports_and_cleanup_without_starting_them(capsys) -> None:
    """A plan that omits cleanup or starts services would violate the approval gate."""
    exit_code = adapter.main(["--plan"])
    plan = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert plan["execution"] == "explicit_commands_only"
    assert plan["services"] == ["redis", "celery-worker", "dramatiq-worker"]
    assert plan["ports"] == [{"host": 6379, "service": "redis"}]
    assert plan["resources"] == {
        "disk": "2 GiB for Redis persistence and logs",
        "memory": "512 MiB Redis; 768 MiB per worker",
    }
    assert plan["cleanup"] == [
        "stop the execution profile after approved collection",
        "remove the dedicated benchmark Redis volume",
        "retain exported event files outside the volume before removal",
    ]
