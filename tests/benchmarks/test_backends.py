import importlib.util
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest

from examples.benchmarks import backends
from examples.benchmarks.models import BackendSpec


def test_backend_module_exposes_the_adapter_contract() -> None:
    """Removing the adapter module must make benchmark execution unavailable."""
    assert importlib.util.find_spec("examples.benchmarks.backends") is not None


def test_executor_adapter_is_available_for_local_executor_contracts() -> None:
    """Removing the local-executor adapter must make its contract untestable."""
    assert hasattr(backends, "ExecutorBackend")


@dataclass
class FakeFuture:
    value: object

    def result(self) -> object:
        return self.value


class FailingFuture:
    def result(self) -> object:
        raise RuntimeError("warmup failure")


class RecordingExecutor:
    def __init__(self) -> None:
        self.submissions: list[bytes] = []
        self.closed = False

    def submit(self, function: object, payload: bytes) -> FakeFuture:
        self.submissions.append(payload)
        return FakeFuture(function(payload))  # type: ignore[operator]

    def shutdown(self, *, wait: bool) -> None:
        assert wait is True
        self.closed = True


class FailingExecutor(RecordingExecutor):
    def submit(self, function: object, payload: bytes) -> FailingFuture:
        return FailingFuture()


class FailingTask:
    def __call__(self, payload: bytes) -> FailingFuture:
        return FailingFuture()


class FailingParallel:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> "FailingParallel":
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    def __call__(self, tasks: object) -> object:
        raise RuntimeError("warmup failure")


class FailingJoblib:
    def __init__(self) -> None:
        self.parallel = FailingParallel()

    def Parallel(self, **kwargs: object) -> FailingParallel:
        return self.parallel

    def delayed(self, function: object) -> object:
        return function


class FailingRemote:
    def remote(self, payload: bytes) -> object:
        return payload


class FailingRay:
    def __init__(self) -> None:
        self.shutdown_called = False

    def is_initialized(self) -> bool:
        return False

    def init(self, **kwargs: object) -> None:
        return None

    def remote(self, function: object) -> FailingRemote:
        return FailingRemote()

    def get(self, refs: object) -> object:
        raise RuntimeError("warmup failure")

    def shutdown(self) -> None:
        self.shutdown_called = True


class FailingDaskClient:
    def __init__(self) -> None:
        self.closed = False

    def submit(
        self, function: object, payload: bytes, **options: object
    ) -> FailingFuture:
        return FailingFuture()

    def close(self) -> None:
        self.closed = True


class SuccessfulParallel:
    def __enter__(self) -> "SuccessfulParallel":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def __call__(self, tasks: object) -> list[object]:
        return [task() for task in tasks]  # type: ignore[union-attr]


class SuccessfulJoblib:
    def Parallel(self, **kwargs: object) -> SuccessfulParallel:
        return SuccessfulParallel()

    def delayed(self, function: object) -> object:
        return lambda *args: lambda: function(*args)  # type: ignore[operator]


class SuccessfulRemote:
    def __init__(self, function: object) -> None:
        self.function = function

    def remote(self, payload: bytes) -> object:
        return self.function(payload)  # type: ignore[operator]


class SuccessfulRay(FailingRay):
    def remote(self, function: object) -> SuccessfulRemote:
        return SuccessfulRemote(function)

    def get(self, refs: object) -> object:
        return refs


class SuccessfulDaskClient(FailingDaskClient):
    def __init__(self) -> None:
        super().__init__()
        self.submit_options: list[dict[str, object]] = []

    def submit(
        self, function: object, payload: bytes, **options: object
    ) -> FakeFuture:
        self.submit_options.append(options)
        return FakeFuture(function(payload))  # type: ignore[operator]


class FakeTask:
    def __init__(self, value_for: object) -> None:
        self.value_for = value_for
        self.scalar_payloads: list[bytes] = []
        self.bulk_payloads: list[tuple[bytes, ...]] = []

    def __call__(self, payload: bytes) -> FakeFuture:
        self.scalar_payloads.append(payload)
        return FakeFuture(self.value_for(payload))  # type: ignore[operator]

    def batch(self, payloads: list[bytes]) -> list[FakeFuture]:
        self.bulk_payloads.append(tuple(payloads))
        return [FakeFuture(self.value_for(payload)) for payload in payloads]  # type: ignore[operator]


def test_executor_backend_awaits_ordered_results_reports_workers_and_closes() -> None:
    """Dropping result retrieval, reordering payloads, or leaking an executor must fail."""
    executor = RecordingExecutor()
    backend = backends.ExecutorBackend(executor, workers=1, process_workers=False)

    results = backend.submit((b"first", b"second"))
    observed = backend.identities()
    backend.close()

    assert results == (b"first", b"second")
    assert observed == {backends.WorkerIdentity(os.getpid(), threading.get_ident())}
    assert backend.worker_count == 1
    assert executor.closed is True


def test_executor_backend_executes_the_declared_workload() -> None:
    """Timing an identity echo instead of the declared workload must fail."""
    executor = RecordingExecutor()
    backend = backends.ExecutorBackend(executor, workers=1, process_workers=False)
    try:
        results = backend.submit_workload("python_cpu", (b"\x00", b"\x01"))
    finally:
        backend.close()

    assert results == (
        backends.run_workload("python_cpu", b"\x00"),
        backends.run_workload("python_cpu", b"\x01"),
    )


def test_failed_warmup_closes_the_executor() -> None:
    """Leaving an executor alive after failed identity verification must fail."""
    executor = FailingExecutor()

    with pytest.raises(RuntimeError, match="warmup failure"):
        backends.ExecutorBackend(executor, workers=1, process_workers=False)

    assert executor.closed is True


def test_thread_warmup_releases_a_timed_out_gate_and_closes() -> None:
    """A serial executor must not deadlock a two-worker warmup gate."""
    executor = RecordingExecutor()

    with pytest.raises(RuntimeError, match="thread warmup gate timed out"):
        backends.ExecutorBackend(
            executor,
            workers=2,
            process_workers=False,
            warmup_attempt_limit=1,
            warmup_timeout_seconds=0.01,
        )

    assert executor.closed is True


def test_thread_warmup_observes_every_worker_before_releasing_tasks() -> None:
    """Short identity tasks must not let one idle thread satisfy a two-worker warmup."""
    executor = ThreadPoolExecutor(max_workers=2)
    backend = backends.ExecutorBackend(
        executor,
        workers=2,
        process_workers=False,
        warmup_timeout_seconds=1,
    )
    try:
        observed = backend.identities()
    finally:
        backend.close()

    assert len(observed) == 2


def test_resource_owning_backends_close_when_warmup_fails() -> None:
    """A warmup failure must release each adapter's acquired resource."""
    pyroxide_closed: list[bool] = []
    with pytest.raises(RuntimeError, match="warmup failure"):
        backends.PyroxideBackend(
            FakeTask(lambda payload: payload),
            FailingTask(),
            workers=1,
            isolated=False,
            close_engine=lambda: pyroxide_closed.append(True),
        )

    joblib = FailingJoblib()
    with pytest.raises(RuntimeError, match="warmup failure"):
        backends.JoblibBackend(joblib, workers=1)

    ray = FailingRay()
    with pytest.raises(RuntimeError, match="warmup failure"):
        backends.RaySingleNodeBackend(ray, workers=1)

    dask = FailingDaskClient()
    with pytest.raises(RuntimeError, match="warmup failure"):
        backends.DaskSingleNodeBackend(dask, workers=1)

    assert pyroxide_closed == [True]
    assert joblib.parallel.closed is True
    assert ray.shutdown_called is True
    assert dask.closed is True


def test_non_stdlib_adapters_report_unavailable_start_methods() -> None:
    """Reporting the host multiprocessing default for another runtime must fail."""
    loky = backends.ExecutorBackend(
        RecordingExecutor(),
        workers=1,
        process_workers=True,
        backend_name="loky",
    )
    joblib = backends.JoblibBackend(SuccessfulJoblib(), workers=1)
    ray = backends.RaySingleNodeBackend(SuccessfulRay(), workers=1)
    dask = backends.DaskSingleNodeBackend(SuccessfulDaskClient(), workers=1)
    try:
        methods = {
            loky.start_method,
            joblib.start_method,
            ray.start_method,
            dask.start_method,
        }
    finally:
        loky.close()
        joblib.close()
        ray.close()
        dask.close()

    assert methods == {"unavailable"}


def test_dask_warmup_disables_pure_task_deduplication() -> None:
    """Identical warmup calls must remain distinct so Dask can exercise every worker."""
    client = SuccessfulDaskClient()
    backend = backends.DaskSingleNodeBackend(client, workers=1)
    backend.close()

    assert client.submit_options == [{"pure": False}]


def test_pyroxide_adapter_keeps_scalar_and_bulk_submission_distinct() -> None:
    """Routing one payload through batch (or many through scalar) must fail."""
    task = FakeTask(lambda payload: payload)
    identity = FakeTask(
        lambda _: backends.WorkerIdentity(os.getpid(), threading.get_ident())
    )
    closed: list[bool] = []
    backend = backends.PyroxideBackend(
        task,
        identity,
        workers=1,
        isolated=False,
        close_engine=lambda: closed.append(True),
    )
    task.scalar_payloads.clear()
    identity.scalar_payloads.clear()

    single = backend.submit((b"one",))
    many = backend.submit((b"two", b"three"))
    backend.close()

    assert single == (b"one",)
    assert many == (b"two", b"three")
    assert task.scalar_payloads == [b"one"]
    assert task.bulk_payloads == [(b"two", b"three")]
    assert backend.identities() == {
        backends.WorkerIdentity(os.getpid(), threading.get_ident())
    }
    assert backend.start_method == "not_applicable"
    assert closed == [True]


def test_pyroxide_adapter_executes_named_workloads_through_the_engine() -> None:
    """Running the workload in the controller would hide Pyroxide execution cost."""
    identity = FakeTask(lambda payload: payload)
    workload = FakeTask(backends._execute_work_item)
    backend = backends.PyroxideBackend(
        identity,
        identity,
        workload_task=workload,
        workers=1,
        isolated=False,
        close_engine=lambda: None,
    )
    try:
        results = backend.submit_workload("payload_echo", (b"one", b"two"))
    finally:
        backend.close()

    assert workload.bulk_payloads == [
        (("payload_echo", b"one"), ("payload_echo", b"two"))
    ]
    assert results == (
        backends.run_workload("payload_echo", b"one"),
        backends.run_workload("payload_echo", b"two"),
    )


def test_thread_pool_factory_uses_the_requested_worker_count() -> None:
    """Ignoring the manifest worker count must fail matched-backend comparisons."""
    backend = backends.create_backend(BackendSpec("threads", "thread_pool", 1))
    try:
        results = backend.submit((b"left", b"right"))
        observed = backend.identities()
    finally:
        backend.close()

    assert results == (b"left", b"right")
    assert len(observed) == 1
    assert backend.worker_count == 1
    assert backend.start_method == "not_applicable"


def test_process_pool_factory_records_its_start_method() -> None:
    """Omitting the actual process start method must fail process comparisons."""
    backend = backends.create_backend(BackendSpec("processes", "process_pool", 1))
    try:
        results = backend.submit((b"process",))
        observed = backend.identities()
    finally:
        backend.close()

    assert results == (b"process",)
    assert len(observed) == 1
    assert backend.worker_count == 1
    assert backend.start_method in {"fork", "forkserver", "spawn"}


def test_loky_is_created_or_rejected_as_an_explicit_optional_backend() -> None:
    """Silently replacing a missing optional backend must fail this comparison contract."""
    try:
        backend = backends.create_backend(BackendSpec("loky", "loky", 1))
    except RuntimeError as error:
        assert str(error).startswith("loky requires optional dependency")
    else:
        try:
            results = backend.submit((b"loky",))
        finally:
            backend.close()
        assert results == (b"loky",)
        assert backend.backend_name == "loky"


@pytest.mark.parametrize(
    "kind",
    [
        "pyroxide_threaded",
        "pyroxide_isolated",
        "interpreter_pool",
        "joblib",
        "ray_single_node",
        "dask_single_node",
    ],
)
def test_backend_spec_names_each_supported_adapter(kind: str) -> None:
    """Collapsing adapters into an unnamed fallback must fail fair comparisons."""
    spec = BackendSpec(kind, kind, 1)

    assert spec.kind == kind


@pytest.mark.parametrize(
    "kind",
    ["interpreter_pool", "joblib", "ray_single_node", "dask_single_node"],
)
def test_optional_backends_never_silently_fall_back(kind: str) -> None:
    """Replacing an unavailable optional adapter with another backend must fail."""
    try:
        backend = backends.create_backend(BackendSpec(kind, kind, 1))
    except RuntimeError as error:
        assert str(error).startswith(f"{kind} requires optional dependency")
    else:
        try:
            assert backend.backend_name == kind
        finally:
            backend.close()


def test_factory_rejects_unknown_backend_kinds_explicitly() -> None:
    """Replacing an unsupported backend with a fallback would invalidate comparisons."""
    spec = object.__new__(BackendSpec)
    object.__setattr__(spec, "id", "unknown")
    object.__setattr__(spec, "kind", "unknown")
    object.__setattr__(spec, "workers", 1)
    object.__setattr__(spec, "recycle_workers", False)

    with pytest.raises(ValueError, match="unsupported backend kind: unknown"):
        backends.create_backend(spec)


def test_factory_rejects_recycling_for_steady_state_adapters() -> None:
    """Allowing a recycling backend would invalidate steady-state claims."""
    spec = BackendSpec("threads", "thread_pool", 1, recycle_workers=True)

    with pytest.raises(ValueError, match="cannot recycle workers"):
        backends.create_backend(spec)
