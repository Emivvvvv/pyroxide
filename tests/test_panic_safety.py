import pytest
from pyroxide import task


@task
def native_task(payload: str) -> None:
    pass


@task
def fail_task(val):
    raise ValueError(f"Intentionally failing with value: {val}")


@task
def add_one(x):
    return x + 1


def test_rust_panic_fails_gracefully_and_engine_survives():
    """
    Verifies that a Rust worker thread panic:
    1. Is caught gracefully and does not crash the host Python process.
    2. Marks the task status as Failed.
    3. Causes .result() to raise a RuntimeError.
    4. Allows subsequent tasks to run successfully without issues.
    """
    # 1. Submit a task designed to trigger a Rust panic
    panic_handle = native_task("TRIGGER_PANIC")

    # 2. Assert result() raises RuntimeError
    with pytest.raises(RuntimeError) as exc_info:
        panic_handle.result()
    assert "panicked" in str(exc_info.value)

    # 3. Assert final status is Failed
    assert panic_handle.status == "Failed"

    # 4. Assert subsequent tasks still run normally
    success_handle = add_one(41)
    res = success_handle.result(consume=False)
    assert res == 42
    assert success_handle.status == "Completed"


def test_python_exception_propagation():
    """
    Verifies that exceptions raised inside Python callables:
    1. Do not crash the background worker thread.
    2. Mark the task status as Failed.
    3. Propagate the error back to the caller when .result() is called.
    """
    handle = fail_task("broken_data")

    # Assert result() raises RuntimeError containing the traceback/message
    with pytest.raises(RuntimeError) as exc_info:
        handle.result()
    err_str = str(exc_info.value)
    assert "Intentionally failing" in err_str
    assert "Original Background Traceback:" in err_str
    assert "test_panic_safety.py" in err_str

    # Assert final status is Failed
    assert handle.status == "Failed"
