import time

import pytest
from pyroxide import group, stats, task


@task
def add_lifecycle_fn(payload):
    a, b = payload
    return a + b


@task(isolated=True)
def multiply_isolated_fn(payload):
    x, y = payload
    return x * y


def test_single_and_batch_submission():
    h1 = add_lifecycle_fn((2, 3))
    assert h1.result(timeout_sec=5.0) == 5

    handles = [add_lifecycle_fn((i, 10)) for i in range(5)]
    tg = group(handles)
    results = tg.result()
    assert results == [10, 11, 12, 13, 14]


def test_isolated_execution():
    h = multiply_isolated_fn((6, 7))
    assert h.result(timeout_sec=10.0) == 42


def test_task_state_transitions():
    @task
    def slow_task(payload):
        time.sleep(0.1)
        return "done"

    h = slow_task(None)
    st = h.status
    assert st in {"Pending", "Running", "Completed"}
    res = h.result(timeout_sec=5.0, consume=False)
    assert res == "done"
    assert h.status == "Completed"


def test_cancellation():
    @task
    def long_task(payload):
        time.sleep(2.0)
        return "finished"

    h = long_task(None)
    # Try cancelling immediately
    cancelled = h.cancel()
    if cancelled:
        assert h.status == "Cancelled"
        with pytest.raises(RuntimeError, match="cancelled"):
            h.result(timeout_sec=1.0)


def test_failure_propagation():
    @task
    def failing_task(payload):
        raise ValueError("Custom failure message")

    h = failing_task(None)
    with pytest.raises(RuntimeError, match="Custom failure message"):
        h.result(timeout_sec=5.0, consume=False)
    assert h.status == "Failed"


def test_result_timeout():
    @task
    def blocked_task(payload):
        time.sleep(1.0)
        return 1

    h = blocked_task(None)
    with pytest.raises(TimeoutError):
        h.result(timeout_sec=0.01)
    # Eventual result still succeeds
    assert h.result(timeout_sec=5.0) == 1


def test_consume_flag_and_close():
    @task
    def dummy(payload):
        return "payload"

    h = dummy(None)
    res1 = h.result(timeout_sec=5.0, consume=False)
    assert res1 == "payload"
    res2 = h.result(timeout_sec=5.0, consume=False)
    assert res2 == "payload"
    res3 = h.result(timeout_sec=5.0, consume=True)
    assert res3 == "payload"
    h.close()


def test_engine_stats():
    st = stats()
    assert isinstance(st, dict)
    assert len(st) > 0
