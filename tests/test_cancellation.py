import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from pyroxide import task

ROOT = Path(__file__).resolve().parents[1]


@task
def native_sleep(payload):
    if isinstance(payload, str) and payload.startswith("SLEEP:"):
        ms = int(payload.split(":")[1])
        time.sleep(ms / 1000.0)


def test_cancel_pending_task():
    env = os.environ.copy()
    env["PYROXIDE_WORKERS"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "python"), str(ROOT), env.get("PYTHONPATH", "")]
    )
    code = textwrap.dedent(
        """
        import threading
        from pyroxide import task

        started = threading.Event()
        release = threading.Event()

        @task
        def blocker(value):
            started.set()
            release.wait()
            return value

        @task
        def pending(value):
            return value

        blocker_handle = blocker(1)
        assert started.wait(5)
        pending_handle = pending(2)
        assert pending_handle.status == "Pending"
        assert pending_handle.cancel() is True
        assert pending_handle.status == "Cancelled"

        release.set()
        assert blocker_handle.result() == 1
        try:
            pending_handle.result()
        except RuntimeError as error:
            assert "Task cancelled" in str(error)
        else:
            raise AssertionError("cancelled pending task returned a result")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_cancel_running_python_task_returns_false_and_preserves_result():
    started = threading.Event()
    release = threading.Event()

    @task
    def controlled(value):
        started.set()
        release.wait()
        return value

    handle = controlled("real result")
    assert started.wait(5)
    assert handle.status == "Running"
    assert handle.cancel() is False
    assert handle.status == "Running"

    release.set()
    assert handle.result() == "real result"


def test_cancel_running_isolated_task_terminates_child():
    from tests.isolated_helper import long_isolated_task_helper

    handle = long_isolated_task_helper(7)
    deadline = time.monotonic() + 5
    while handle.status == "Pending":
        assert time.monotonic() < deadline
        time.sleep(0.001)

    assert handle.status == "Running"
    assert handle.cancel() is True
    assert handle.status == "Cancelled"
    with pytest.raises(RuntimeError, match="Task cancelled"):
        handle.result()


@pytest.mark.skipif(sys.platform == "win32", reason="uses Unix PID probing")
def test_cancelled_isolated_result_waits_for_child_exit(tmp_path):
    from tests.isolated_helper import report_pid_then_sleep

    pid_path = tmp_path / "worker.pid"
    handle = report_pid_then_sleep((str(pid_path), 5))
    deadline = time.monotonic() + 5
    while not pid_path.exists():
        assert time.monotonic() < deadline
        time.sleep(0.001)

    pid = int(pid_path.read_text())
    assert handle.status == "Running"
    assert handle.cancel() is True
    with pytest.raises(RuntimeError, match="Task cancelled"):
        handle.result()

    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cancel_already_finished_task():
    handle = native_sleep("SLEEP:1")
    handle.result(consume=False)
    assert handle.status == "Completed"

    # Cannot cancel completed task
    assert handle.cancel() is False
    assert handle.status == "Completed"
