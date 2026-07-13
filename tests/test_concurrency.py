import time
import pytest
from pyroxide import task


from typing import Any


@task
def native_sleep(payload: Any) -> Any:
    if isinstance(payload, str):
        if payload.startswith("SLEEP:"):
            ms = int(payload.split(":")[1])
            time.sleep(ms / 1000.0)
            return payload
        return payload.upper()
    elif isinstance(payload, (bytes, memoryview)):
        return bytes(payload).upper()
    else:
        raise RuntimeError("Unsupported payload type")


@task
def python_square(x: int) -> int:
    return x * x


def test_native_parallel_execution():
    """
    Submits 4 native tasks that sleep for 100ms each.
    If the GIL is correctly released and tasks run concurrently on the 4 workers,
    the total time taken to wait for all tasks should be close to 100ms (definitely < 200ms).
    If they are serialized, it would take >= 400ms.
    """
    # Warm up worker threads
    native_sleep("SLEEP:1").wait()

    start_time = time.time()
    handles = [native_sleep("SLEEP:100") for _ in range(4)]

    # Wait for all handles
    statuses = [h.wait() for h in handles]
    duration = time.time() - start_time

    assert all(status == "Completed" for status in statuses)
    assert duration < 0.35, (
        f"Expected parallel execution to take < 0.35s, took {duration:.4f}s"
    )


def test_gil_unlocked_main_thread():
    """
    Submits a native task that sleeps for 500ms.
    Verifies that the main Python thread is not blocked by the background worker
    and can execute Python code concurrently.
    """
    # Submit a long-running native sleep task
    handle = native_sleep("SLEEP:500")

    # Measure responsiveness of main thread
    start_time = time.time()
    # Perform a local operation that should be instant
    local_calc = sum(i * i for i in range(1000))
    elapsed = time.time() - start_time

    assert local_calc == 332833500
    assert elapsed < 0.05, f"Main thread blocked! Calculation took {elapsed:.4f}s"

    # Verify the background task eventually completes
    status = handle.wait(timeout_sec=1.0)
    assert status == "Completed"


def test_python_callable_execution():
    """
    Verifies that a standard Python callable can be executed on background workers
    and its result retrieved correctly.
    """
    handle = python_square(12)
    assert handle.status in ("Pending", "Running", "Completed")

    result = handle.result(timeout_sec=1.0, consume=False)
    assert result == 144
    assert handle.status == "Completed"


def test_native_task_timeout():
    """
    Verifies that calling handle.wait() or handle.result() with a timeout
    raises TimeoutError if the task does not finish within the timeout period.
    """
    # Submit a task that sleeps for 500ms
    handle = native_sleep("SLEEP:500")

    # Wait with 50ms timeout should raise TimeoutError
    with pytest.raises(TimeoutError) as exc_info:
        handle.wait(timeout_sec=0.05)
    assert "timed out" in str(exc_info.value)


def test_native_task_bytes_and_memoryview():
    """
    Verifies that native tasks can process raw bytes and memoryview payloads.
    """
    # 1. Test bytes payload
    payload_bytes = b"hello bytes"
    handle1 = native_sleep(payload_bytes)
    res1 = handle1.result(consume=False)
    assert res1 == b"HELLO BYTES"

    # 2. Test memoryview payload
    payload_mv = memoryview(b"hello memoryview")
    handle2 = native_sleep(payload_mv)
    res2 = handle2.result(consume=False)
    assert res2 == b"HELLO MEMORYVIEW"


def test_native_task_invalid_payload():
    """
    Verifies that passing an unsupported payload type (e.g. dict) to a native task
    fails gracefully with a RuntimeError.
    """
    handle = native_sleep({"key": "value"})  # dict is not supported natively
    with pytest.raises(RuntimeError) as exc_info:
        handle.result()
    assert "Unsupported payload type" in str(exc_info.value)
    assert handle.status == "Failed"


def test_async_result_waiting():
    import asyncio

    async def main():
        handle = native_sleep("SLEEP:100")
        start_time = time.time()
        res = await handle.result_async(timeout_sec=1.0)
        duration = time.time() - start_time
        return res, duration

    res, duration = asyncio.run(main())
    assert res == "SLEEP:100"
    assert duration >= 0.09


def test_batch_submission():
    payloads = [1, 2, 3, 4, 5]
    handles = python_square.batch(payloads)

    assert len(handles) == 5

    results = [h.result() for h in handles]
    assert results == [1, 4, 9, 16, 25]


def test_batch_submission_native():
    payloads = ["a", "b", "c"]
    handles = native_sleep.batch(payloads)

    assert len(handles) == 3
    results = [h.result() for h in handles]
    assert results == ["A", "B", "C"]


def test_batch_submission_native_bytes():
    payloads = [b"a", b"b", b"c"]
    handles = native_sleep.batch(payloads)

    assert len(handles) == 3
    results = [h.result() for h in handles]
    assert results == [b"A", b"B", b"C"]


def test_task_group_workflow():
    from pyroxide import group
    
    # 1. Test parallel execution group
    payloads = [5, 10, 15]
    handles = python_square.batch(payloads)
    
    tg = group(handles)
    assert tg.status in ("Running", "Completed")
    
    results = tg.result(consume=False)
    assert results == [25, 100, 225]
    assert tg.status == "Completed"
    
    # 2. Test group cancellation
    h_cancel = python_square.batch([20, 30])
    tg_cancel = group(h_cancel)
    tg_cancel.cancel()
    assert tg_cancel.status == "Cancelled"
