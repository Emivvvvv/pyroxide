import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_child(code: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(overrides)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "python"), str(ROOT), env.get("PYTHONPATH", "")]
    )
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_shutdown_drains_tasks_and_rejects_new_submissions():
    result = run_child(
        """
        import pyroxide
        from pyroxide import task

        @task
        def double(value):
            return value * 2

        handles = [double(value) for value in range(20)]
        pyroxide.shutdown(wait=True)
        assert [handle.result() for handle in handles] == [value * 2 for value in range(20)]

        pyroxide.shutdown(wait=True)
        try:
            double(21)
        except RuntimeError as error:
            assert "shut down" in str(error).lower()
        else:
            raise AssertionError("submission after shutdown succeeded")
        """
    )
    assert result.returncode == 0, result.stderr


def test_shutdown_can_cancel_pending_tasks():
    result = run_child(
        """
        import time
        import pyroxide
        from pyroxide import task

        @task
        def slow(value):
            time.sleep(0.2)
            return value

        running = slow(1)
        deadline = time.monotonic() + 2
        while running.status == "Pending":
            assert time.monotonic() < deadline
            time.sleep(0.001)

        pending = slow(2)
        pyroxide.shutdown(wait=True, cancel_pending=True)

        assert running.result() == 1
        try:
            pending.result()
        except RuntimeError as error:
            assert "cancelled" in str(error).lower()
        else:
            raise AssertionError("pending task was not cancelled")
        """,
        PYROXIDE_WORKERS="1",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("isolated", [False, True])
def test_shutdown_wins_the_controlled_start_claim_gap_in_each_worker_mode(
    tmp_path, isolated
):
    marker_path = tmp_path / f"executed-{isolated}"
    result = run_child(
        f"""
        from pathlib import Path

        import pyroxide
        from pyroxide import task
        from pyroxide._pyroxide import (
            _arm_start_claim_test_hook,
            _resume_start_claim_test_hook,
            _wait_start_claim_test_hook,
        )

        @task(isolated={isolated!r})
        def mark_execution(path):
            Path(path).write_text("executed", encoding="utf-8")
            return path

        _arm_start_claim_test_hook()
        handle = mark_execution({str(marker_path)!r})
        reached_isolated_loop = _wait_start_claim_test_hook()
        assert reached_isolated_loop is {isolated!r}
        pyroxide.shutdown(wait=False, cancel_pending=True)
        _resume_start_claim_test_hook()

        try:
            handle.result(timeout_sec=1)
        except RuntimeError as error:
            assert "cancelled" in str(error).lower()
        else:
            raise AssertionError("task crossed the shutdown claim boundary")

        pyroxide.shutdown(wait=True)
        assert not Path({str(marker_path)!r}).exists()
        """,
        PYROXIDE_WORKERS="1",
        PYROXIDE_MAX_PROCESSES="1",
    )
    assert result.returncode == 0, result.stderr


def test_shutdown_wait_false_returns_before_running_task_finishes():
    result = run_child(
        """
        import time
        import pyroxide
        from pyroxide import task

        @task
        def slow(value):
            time.sleep(0.5)
            return value

        handle = slow(7)
        deadline = time.monotonic() + 2
        while handle.status == "Pending":
            assert time.monotonic() < deadline
            time.sleep(0.001)

        started = time.monotonic()
        pyroxide.shutdown(wait=False)
        assert time.monotonic() - started < 0.2
        assert handle.result() == 7
        pyroxide.shutdown(wait=True)
        """,
        PYROXIDE_WORKERS="1",
    )
    assert result.returncode == 0, result.stderr


def test_shutdown_wait_true_is_rejected_inside_worker_task():
    result = run_child(
        """
        import os
        import pyroxide
        from pyroxide import task

        @task
        def stop_from_worker(_):
            pyroxide.shutdown(wait=True)

        handle = stop_from_worker(None)
        try:
            handle.result(timeout_sec=1)
        except RuntimeError as error:
            assert "worker" in str(error).lower()
        except TimeoutError:
            os._exit(4)
        else:
            raise AssertionError("worker shutdown unexpectedly succeeded")

        @task
        def stop_without_wait(_):
            pyroxide.shutdown(wait=False)
            return "stopping"

        assert stop_without_wait(None).result(timeout_sec=1) == "stopping"
        pyroxide.shutdown(wait=True)
        """
    )
    assert result.returncode == 0, result.stderr


def test_submission_racing_shutdown_is_rejected_or_drained():
    result = run_child(
        """
        import threading
        import time

        import pyroxide
        from pyroxide import task

        @task
        def echo(value):
            return value

        started = threading.Event()
        outcome = {}
        payloads = list(range(20_000))

        def submit_batch():
            started.set()
            try:
                outcome["handles"] = echo.batch(payloads)
            except RuntimeError as error:
                assert "shut down" in str(error).lower()
                outcome["rejected"] = True

        submitter = threading.Thread(target=submit_batch)
        submitter.start()
        assert started.wait(2)
        time.sleep(0.001)
        pyroxide.shutdown(wait=False)
        submitter.join(5)
        assert not submitter.is_alive()
        pyroxide.shutdown(wait=True)

        if "handles" in outcome:
            handles = outcome["handles"]
            assert [handle.result(timeout_sec=2) for handle in handles] == payloads
        else:
            assert outcome.get("rejected") is True
        """,
        PYROXIDE_QUEUE_CAPACITY="20000",
        PYROXIDE_QUEUE_TIMEOUT_MS="0",
        PYROXIDE_WORKERS="8",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_initialized_engine_is_rejected_after_fork():
    result = run_child(
        """
        import os
        import pyroxide
        from pyroxide import task

        @task
        def echo(value):
            return value

        assert echo(1).result() == 1

        child_pid = os.fork()
        if child_pid == 0:
            try:
                echo(2)
            except pyroxide.ForkSafetyError:
                os._exit(0)
            except BaseException:
                os._exit(2)
            os._exit(3)

        _, status = os.waitpid(child_pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        pyroxide.shutdown()
        """
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_registered_wasm_runtime_is_rejected_after_fork():
    result = run_child(
        """
        import os
        import pyroxide

        wat = '''
        (module
          (memory (export "memory") 1)
          (func (export "run") (param i32 i32) (result i64) i64.const 0)
          (func (export "alloc") (param i32) (result i32) i32.const 0)
          (func (export "dealloc") (param i32 i32))
        )
        '''
        pyroxide.register_wasm_wat("before_fork", wat)

        child_pid = os.fork()
        if child_pid == 0:
            try:
                pyroxide.register_wasm_wat("after_fork", wat)
            except pyroxide.ForkSafetyError:
                os._exit(0)
            except BaseException:
                os._exit(2)
            os._exit(3)

        _, status = os.waitpid(child_pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        """
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name != "posix", reason="Unix socket permissions")
def test_isolated_socket_uses_private_directory_and_is_cleaned_up():
    result = run_child(
        """
        import os
        import stat
        import tempfile
        import time
        from pathlib import Path

        import pyroxide
        from tests.isolated_helper import report_pid_then_sleep

        temp_root = Path(tempfile.gettempdir())
        before = set(temp_root.glob("pyroxide-ipc-*"))
        pid_file = temp_root / f"pyroxide-worker-{os.getpid()}.pid"
        handle = report_pid_then_sleep((str(pid_file), 0.3))

        deadline = time.monotonic() + 3
        while not pid_file.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)

        created = set(temp_root.glob("pyroxide-ipc-*")) - before
        assert len(created) == 1, created
        socket_dir = created.pop()
        assert stat.S_IMODE(socket_dir.stat().st_mode) == 0o700
        assert handle.result() > 0

        pyroxide.shutdown(wait=True)
        assert not socket_dir.exists()
        pid_file.unlink(missing_ok=True)
        """
    )
    assert result.returncode == 0, result.stderr
