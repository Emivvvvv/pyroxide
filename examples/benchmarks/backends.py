"""Matched local executor adapters for benchmark correctness checks."""

from __future__ import annotations

import importlib
import multiprocessing
import os
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

try:
    from .models import BackendSpec
    from .workloads import run_workload, worker_identity
except ImportError:  # pragma: no cover - exercised by script entry points.
    from models import BackendSpec
    from workloads import run_workload, worker_identity

_WARMUP_ATTEMPT_LIMIT = 16


@dataclass(frozen=True, slots=True)
class WorkerIdentity:
    """One worker observed while warming a backend."""

    pid: int
    thread_id: int


class Backend(Protocol):
    def submit(self, payloads: Sequence[bytes]) -> Sequence[bytes]: ...

    def submit_workload(
        self, workload_name: str, payloads: Sequence[bytes]
    ) -> Sequence[bytes]: ...

    def identities(self) -> set[WorkerIdentity]: ...

    def close(self) -> None: ...


class _ThreadWarmupGate:
    """Release a bounded thread warmup only after every worker has arrived."""

    def __init__(self, workers: int, timeout_seconds: float) -> None:
        self._workers = workers
        self._timeout_seconds = timeout_seconds
        self._arrived = 0
        self._released = False
        self._timed_out = False
        self._condition = threading.Condition()

    def wait(self) -> bool:
        with self._condition:
            self._arrived += 1
            if self._arrived == self._workers:
                self._released = True
                self._condition.notify_all()
                return True
            deadline = time.monotonic() + self._timeout_seconds
            while not self._released:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            if not self._released:
                self._timed_out = True
                self._released = True
                self._condition.notify_all()
            return not self._timed_out


class ExecutorBackend:
    """Adapter for executors whose submissions return future-like objects."""

    def __init__(
        self,
        executor: Any,
        *,
        workers: int,
        process_workers: bool,
        backend_name: str = "executor",
        warmup_attempt_limit: int = _WARMUP_ATTEMPT_LIMIT,
        warmup_timeout_seconds: float = 1.0,
        thread_warmup: bool | None = None,
    ) -> None:
        self._executor = executor
        self.worker_count = workers
        self.backend_name = backend_name
        self.process_workers = process_workers
        self._thread_warmup = (
            not process_workers if thread_warmup is None else thread_warmup
        )
        self.start_method = (
            multiprocessing.get_context().get_start_method()
            if backend_name == "process_pool"
            else "unavailable"
            if process_workers
            else "not_applicable"
        )
        self._identities: set[WorkerIdentity] = set()
        self._closed = False
        try:
            self._warmup(warmup_attempt_limit, warmup_timeout_seconds)
        except Exception:
            self.close()
            raise

    def submit(self, payloads: Sequence[bytes]) -> Sequence[bytes]:
        futures = [self._executor.submit(worker_identity, payload) for payload in payloads]
        return tuple(future.result() for future in futures)

    def submit_workload(
        self, workload_name: str, payloads: Sequence[bytes]
    ) -> Sequence[bytes]:
        futures = [
            self._executor.submit(_execute_work_item, (workload_name, payload))
            for payload in payloads
        ]
        return tuple(future.result() for future in futures)

    def identities(self) -> set[WorkerIdentity]:
        return set(self._identities)

    def close(self) -> None:
        if not self._closed:
            self._executor.shutdown(wait=True)
            self._closed = True

    def _warmup(self, attempt_limit: int, timeout_seconds: float) -> None:
        if attempt_limit <= 0:
            raise ValueError("warmup_attempt_limit must be positive")
        if timeout_seconds <= 0:
            raise ValueError("warmup_timeout_seconds must be positive")
        for _ in range(attempt_limit):
            if self._thread_warmup:
                gate = _ThreadWarmupGate(self.worker_count, timeout_seconds)
                futures = [
                    self._executor.submit(_observe_thread_worker_identity, gate)
                    for _ in range(self.worker_count)
                ]
                observations = tuple(future.result() for future in futures)
                self._identities.update(identity for identity, _ in observations)
                if not all(released for _, released in observations):
                    raise RuntimeError("thread warmup gate timed out")
                if len(self._identities) >= self.worker_count:
                    return
                continue
            futures = [
                self._executor.submit(_observe_worker_identity_slow, b"")
                for _ in range(self.worker_count)
            ]
            self._identities.update(future.result() for future in futures)
            if len(self._identities) >= self.worker_count:
                return
        raise RuntimeError(
            "warmup did not observe all configured workers "
            f"({len(self._identities)} of {self.worker_count}) within {attempt_limit} attempts"
        )


def _observe_worker_identity(payload: bytes) -> WorkerIdentity:
    worker_identity(payload)
    return WorkerIdentity(os.getpid(), threading.get_ident())


def _observe_worker_identity_slow(payload: bytes) -> WorkerIdentity:
    """Keep warmup work in flight long enough for schedulers to start all workers."""
    identity = _observe_worker_identity(payload)
    time.sleep(0.01)
    return identity


def _observe_thread_worker_identity(
    gate: _ThreadWarmupGate,
) -> tuple[WorkerIdentity, bool]:
    return _observe_worker_identity(b""), gate.wait()


def _execute_work_item(item: tuple[str, bytes]) -> bytes:
    """Execute one serializable named workload item."""
    workload_name, payload = item
    return run_workload(workload_name, payload)


class PyroxideBackend:
    """Adapter preserving Pyroxide's scalar and batch submission APIs."""

    def __init__(
        self,
        task: Any,
        identity_task: Any,
        *,
        workload_task: Any | None = None,
        workers: int,
        isolated: bool,
        close_engine: Any,
        warmup_attempt_limit: int = _WARMUP_ATTEMPT_LIMIT,
    ) -> None:
        self._task = task
        self._identity_task = identity_task
        self._workload_task = workload_task
        self._close_engine = close_engine
        self.worker_count = workers
        self.backend_name = "pyroxide_isolated" if isolated else "pyroxide_threaded"
        self.isolated = isolated
        self.start_method = "unavailable" if isolated else "not_applicable"
        self._identities: set[WorkerIdentity] = set()
        self._closed = False
        try:
            self._warmup(warmup_attempt_limit)
        except Exception:
            self.close()
            raise

    def submit(self, payloads: Sequence[bytes]) -> Sequence[bytes]:
        if len(payloads) == 1:
            handles = (self._task(payloads[0]),)
        else:
            handles = tuple(self._task.batch(list(payloads)))
        return tuple(handle.result() for handle in handles)

    def submit_workload(
        self, workload_name: str, payloads: Sequence[bytes]
    ) -> Sequence[bytes]:
        if self._workload_task is None:
            raise RuntimeError("Pyroxide workload task is not configured")
        items = [(workload_name, payload) for payload in payloads]
        if len(items) == 1:
            handles = (self._workload_task(items[0]),)
        else:
            handles = tuple(self._workload_task.batch(items))
        return tuple(handle.result() for handle in handles)

    def identities(self) -> set[WorkerIdentity]:
        return set(self._identities)

    def close(self) -> None:
        if not self._closed:
            self._close_engine()
            self._closed = True

    def _warmup(self, attempt_limit: int) -> None:
        if attempt_limit <= 0:
            raise ValueError("warmup_attempt_limit must be positive")
        for _ in range(attempt_limit):
            handles = [self._identity_task(b"") for _ in range(self.worker_count)]
            self._identities.update(handle.result() for handle in handles)
            if len(self._identities) >= self.worker_count:
                return
        raise RuntimeError(
            "warmup did not observe all configured workers "
            f"({len(self._identities)} of {self.worker_count}) within {attempt_limit} attempts"
        )


class JoblibBackend:
    """Single-node joblib adapter with process-worker warmup verification."""

    def __init__(self, joblib: Any, *, workers: int) -> None:
        self._joblib = joblib
        self._parallel = joblib.Parallel(n_jobs=workers, backend="loky")
        self._parallel.__enter__()
        self.worker_count = workers
        self.backend_name = "joblib"
        self.start_method = "unavailable"
        self._identities: set[WorkerIdentity] = set()
        self._closed = False
        try:
            self._warmup()
        except Exception:
            self.close()
            raise

    def submit(self, payloads: Sequence[bytes]) -> Sequence[bytes]:
        return tuple(
            self._parallel(
                self._joblib.delayed(worker_identity)(payload) for payload in payloads
            )
        )

    def submit_workload(
        self, workload_name: str, payloads: Sequence[bytes]
    ) -> Sequence[bytes]:
        return tuple(
            self._parallel(
                self._joblib.delayed(run_workload)(workload_name, payload)
                for payload in payloads
            )
        )

    def identities(self) -> set[WorkerIdentity]:
        return set(self._identities)

    def close(self) -> None:
        if not self._closed:
            self._parallel.__exit__(None, None, None)
            self._closed = True

    def _warmup(self) -> None:
        for _ in range(_WARMUP_ATTEMPT_LIMIT):
            self._identities.update(
                self._parallel(
                    self._joblib.delayed(_observe_worker_identity_slow)(b"")
                    for _ in range(self.worker_count)
                )
            )
            if len(self._identities) >= self.worker_count:
                return
        raise RuntimeError(
            "warmup did not observe all configured workers "
            f"({len(self._identities)} of {self.worker_count}) within "
            f"{_WARMUP_ATTEMPT_LIMIT} attempts"
        )


class RaySingleNodeBackend:
    """Explicitly labelled single-node Ray batch adapter."""

    def __init__(self, ray: Any, *, workers: int) -> None:
        if ray.is_initialized():
            raise RuntimeError("ray_single_node requires a fresh Ray runtime")
        self._ray = ray
        self.worker_count = workers
        self.backend_name = "ray_single_node"
        self.start_method = "unavailable"
        self._identities: set[WorkerIdentity] = set()
        self._closed = False
        try:
            ray.init(num_cpus=workers, include_dashboard=False)
            self._payload_task = ray.remote(worker_identity)
            self._workload_task = ray.remote(run_workload)
            self._identity_task = ray.remote(_observe_worker_identity_slow)
            self._warmup()
        except Exception:
            self.close()
            raise

    def submit(self, payloads: Sequence[bytes]) -> Sequence[bytes]:
        return tuple(self._ray.get([self._payload_task.remote(payload) for payload in payloads]))

    def submit_workload(
        self, workload_name: str, payloads: Sequence[bytes]
    ) -> Sequence[bytes]:
        return tuple(
            self._ray.get(
                [
                    self._workload_task.remote(workload_name, payload)
                    for payload in payloads
                ]
            )
        )

    def identities(self) -> set[WorkerIdentity]:
        return set(self._identities)

    def close(self) -> None:
        if not self._closed:
            self._ray.shutdown()
            self._closed = True

    def _warmup(self) -> None:
        for _ in range(_WARMUP_ATTEMPT_LIMIT):
            self._identities.update(
                self._ray.get(
                    [self._identity_task.remote(b"") for _ in range(self.worker_count)]
                )
            )
            if len(self._identities) >= self.worker_count:
                return
        raise RuntimeError(
            "warmup did not observe all configured workers "
            f"({len(self._identities)} of {self.worker_count}) within "
            f"{_WARMUP_ATTEMPT_LIMIT} attempts"
        )


class DaskSingleNodeBackend:
    """Explicitly labelled single-node Dask batch adapter."""

    def __init__(self, client: Any, *, workers: int) -> None:
        self._client = client
        self.worker_count = workers
        self.backend_name = "dask_single_node"
        self.start_method = "unavailable"
        self._identities: set[WorkerIdentity] = set()
        self._closed = False
        try:
            self._warmup()
        except Exception:
            self.close()
            raise

    def submit(self, payloads: Sequence[bytes]) -> Sequence[bytes]:
        futures = [self._client.submit(worker_identity, payload) for payload in payloads]
        return tuple(future.result() for future in futures)

    def submit_workload(
        self, workload_name: str, payloads: Sequence[bytes]
    ) -> Sequence[bytes]:
        futures = [
            self._client.submit(run_workload, workload_name, payload)
            for payload in payloads
        ]
        return tuple(future.result() for future in futures)

    def identities(self) -> set[WorkerIdentity]:
        return set(self._identities)

    def close(self) -> None:
        if not self._closed:
            self._client.close()
            self._closed = True

    def _warmup(self) -> None:
        for _ in range(_WARMUP_ATTEMPT_LIMIT):
            futures = [
                self._client.submit(
                    _observe_worker_identity_slow,
                    b"",
                    pure=False,
                )
                for _ in range(self.worker_count)
            ]
            self._identities.update(future.result() for future in futures)
            if len(self._identities) >= self.worker_count:
                return
        raise RuntimeError(
            "warmup did not observe all configured workers "
            f"({len(self._identities)} of {self.worker_count}) within "
            f"{_WARMUP_ATTEMPT_LIMIT} attempts"
        )


def create_backend(spec: BackendSpec) -> Backend:
    """Create a backend configured by a benchmark specification."""
    if spec.recycle_workers:
        raise ValueError("steady-state backend adapters cannot recycle workers")
    if spec.kind in {"pyroxide", "pyroxide_threaded", "pyroxide_isolated"}:
        return _create_pyroxide_backend(
            spec,
            isolated=spec.kind == "pyroxide_isolated",
        )
    if spec.kind == "thread_pool":
        return ExecutorBackend(
            ThreadPoolExecutor(max_workers=spec.workers),
            workers=spec.workers,
            process_workers=False,
            backend_name=spec.kind,
        )
    if spec.kind == "process_pool":
        return ExecutorBackend(
            ProcessPoolExecutor(max_workers=spec.workers),
            workers=spec.workers,
            process_workers=True,
            backend_name=spec.kind,
        )
    if spec.kind == "interpreter_pool":
        executor_type = getattr(importlib.import_module("concurrent.futures"), "InterpreterPoolExecutor", None)
        if executor_type is None:
            raise RuntimeError(
                "interpreter_pool requires optional dependency: "
                "concurrent.futures.InterpreterPoolExecutor"
            )
        return ExecutorBackend(
            executor_type(max_workers=spec.workers),
            workers=spec.workers,
            process_workers=False,
            backend_name=spec.kind,
            thread_warmup=False,
        )
    if spec.kind == "loky":
        try:
            loky = importlib.import_module("loky")
        except ImportError as error:
            raise RuntimeError(f"loky requires optional dependency: {error}") from error
        return ExecutorBackend(
            loky.get_reusable_executor(max_workers=spec.workers),
            workers=spec.workers,
            process_workers=True,
            backend_name=spec.kind,
        )
    if spec.kind == "joblib":
        try:
            joblib = importlib.import_module("joblib")
        except ImportError as error:
            raise RuntimeError(f"joblib requires optional dependency: {error}") from error
        return JoblibBackend(joblib, workers=spec.workers)
    if spec.kind == "ray_single_node":
        try:
            ray = importlib.import_module("ray")
        except ImportError as error:
            raise RuntimeError(
                f"ray_single_node requires optional dependency: {error}"
            ) from error
        return RaySingleNodeBackend(ray, workers=spec.workers)
    if spec.kind == "dask_single_node":
        try:
            distributed = importlib.import_module("distributed")
        except ImportError as error:
            raise RuntimeError(
                f"dask_single_node requires optional dependency: {error}"
            ) from error
        client = distributed.Client(
            n_workers=spec.workers,
            threads_per_worker=1,
            processes=True,
            dashboard_address=None,
        )
        return DaskSingleNodeBackend(client, workers=spec.workers)
    raise ValueError(f"unsupported backend kind: {spec.kind}")


def _create_pyroxide_backend(spec: BackendSpec, *, isolated: bool) -> PyroxideBackend:
    if "pyroxide" in sys.modules:
        raise RuntimeError("Pyroxide must be configured before import/config lock")
    _set_pyroxide_environment("PYROXIDE_WORKERS", spec.workers)
    _set_pyroxide_environment("PYROXIDE_MAX_PROCESSES", spec.workers)
    _set_pyroxide_environment("PYROXIDE_MAX_TASKS_PER_WORKER", 0)
    pyroxide = importlib.import_module("pyroxide")
    task = pyroxide.task
    return PyroxideBackend(
        task(worker_identity, isolated=isolated),
        task(_observe_worker_identity_slow, isolated=isolated),
        workload_task=task(_execute_work_item, isolated=isolated),
        workers=spec.workers,
        isolated=isolated,
        close_engine=pyroxide.shutdown,
    )


def _set_pyroxide_environment(name: str, value: int) -> None:
    configured = os.environ.get(name)
    expected = str(value)
    if configured is None:
        os.environ[name] = expected
    elif configured != expected:
        raise RuntimeError(f"{name} must equal {expected} for matched Pyroxide workers")
