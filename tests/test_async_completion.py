import asyncio
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from pyroxide import task
from pyroxide._pyroxide import get_status
from pyroxide.types import TaskHandle

ROOT = Path(__file__).resolve().parents[1]


def test_result_async_cannot_lose_completion(monkeypatch):
    release = threading.Event()

    @task
    def wait_for_release(value):
        release.wait()
        return value

    handle = wait_for_release(42)
    original_status = TaskHandle.status
    injected = False

    def stale_status(self):
        nonlocal injected
        if self.task_id == handle.task_id and not injected:
            injected = True
            release.set()
            deadline = time.monotonic() + 2
            while get_status(self.task_id) != "Completed":
                assert time.monotonic() < deadline
                time.sleep(0.001)
            return "Running"
        return original_status.__get__(self, TaskHandle)

    monkeypatch.setattr(TaskHandle, "status", property(stale_status))

    async def run():
        return await asyncio.wait_for(handle.result_async(timeout_sec=0.5), timeout=1)

    assert asyncio.run(run()) == 42


def test_result_async_rejects_a_second_concurrent_waiter():
    @task
    def delayed(value):
        time.sleep(0.05)
        return value

    async def run():
        handle = delayed(7)
        first = asyncio.create_task(handle.result_async(timeout_sec=1))
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="already being awaited"):
            await handle.result_async(timeout_sec=1)
        assert await asyncio.wait_for(first, timeout=1) == 7

    asyncio.run(run())


@pytest.mark.skipif(sys.platform == "win32", reason="Unix pipe waker only")
def test_async_waker_can_be_reinitialized():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "python"), str(ROOT), env.get("PYTHONPATH", "")]
    )
    code = textwrap.dedent(
        """
        import asyncio
        import os
        import time

        import pyroxide.types as handle_types
        from pyroxide import task

        @task
        def delayed(value):
            time.sleep(0.05)
            return value

        async def run():
            loop = asyncio.get_running_loop()
            handle_types.ensure_waker_registered(loop)
            assert handle_types._waker_w is not None
            assert os.get_blocking(handle_types._waker_w) is False
            handle_types._cleanup_waker()

            handle = delayed(9)
            return await asyncio.wait_for(
                handle.result_async(timeout_sec=0.5), timeout=1
            )

        assert asyncio.run(run()) == 9
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


@pytest.mark.skipif(sys.platform == "win32", reason="Unix pipe waker only")
def test_shutdown_resolves_an_active_async_waiter_before_waker_teardown():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "python"), str(ROOT), env.get("PYTHONPATH", "")]
    )
    env["PYROXIDE_WORKERS"] = "1"
    code = textwrap.dedent(
        """
        import asyncio
        import threading

        import pyroxide
        import pyroxide.types as handle_types
        from pyroxide import task

        worker_release = threading.Event()
        waker_started = threading.Event()
        waker_release = threading.Event()
        shutdown_started = threading.Event()
        shutdown_done = threading.Event()
        shutdown_errors = []

        def gated_waker_loop():
            waker_started.set()
            waker_release.wait(5)

        handle_types._waker_thread_loop = gated_waker_loop

        @task
        def wait_for_release(value):
            worker_release.wait()
            return value

        async def run():
            handle = wait_for_release(73)
            waiter = asyncio.create_task(handle.result_async())

            deadline = asyncio.get_running_loop().time() + 1
            while True:
                with handle_types._pending_futures_lock:
                    is_pending = handle.task_id in handle_types._pending_futures
                if is_pending and waker_started.is_set():
                    break
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0)

            def shut_down():
                shutdown_started.set()
                try:
                    pyroxide.shutdown(wait=True)
                except BaseException as error:
                    shutdown_errors.append(error)
                finally:
                    shutdown_done.set()

            shutdown_thread = threading.Thread(target=shut_down)
            shutdown_thread.start()
            assert shutdown_started.wait(1)
            worker_release.set()

            try:
                result = await asyncio.wait_for(waiter, timeout=1)
                assert result == 73
                assert shutdown_done.wait(1)
                assert shutdown_errors == []
            finally:
                waker_release.set()
                shutdown_thread.join(2)
                assert not shutdown_thread.is_alive()

        asyncio.run(run())
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
