import time
import pytest
from pyroxide import task


@task
def dummy_square(x):
    time.sleep(0.05)
    return x * x


@task
def native_sleep(payload):
    if isinstance(payload, str) and payload.startswith("SLEEP:"):
        ms = int(payload.split(":")[1])
        time.sleep(ms / 1000.0)


def test_cancel_pending_task():
    # Submit task
    handle = dummy_square(5)
    assert handle.status in ("Pending", "Running")

    # Try to cancel
    cancelled = handle.cancel()

    # If it was still pending/running, cancel should return True
    if cancelled:
        assert handle.status == "Cancelled"
        with pytest.raises(RuntimeError, match="Task cancelled"):
            handle.result()
    else:
        # If it finished before we could cancel, result should succeed
        assert handle.status == "Completed"
        assert handle.result() == 25


def test_cancel_running_native_task():
    # Submit native task with long sleep (e.g. 500ms)
    handle = native_sleep("SLEEP:500")

    # Wait a tiny bit to ensure it enters execution loop
    time.sleep(0.05)

    # Cancel it
    assert handle.cancel() is True
    assert handle.status == "Cancelled"

    with pytest.raises(RuntimeError, match="Task cancelled"):
        handle.result()


def test_cancel_already_finished_task():
    handle = native_sleep("SLEEP:1")
    handle.result(consume=False)
    assert handle.status == "Completed"

    # Cannot cancel completed task
    assert handle.cancel() is False
    assert handle.status == "Completed"
