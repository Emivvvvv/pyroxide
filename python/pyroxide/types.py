"""Task handle public implementation."""

import asyncio
import sys
from typing import Any, Optional

from ._async_waker import (
    _async_waiters,
    _cleanup_waker,
    _pending_futures,
    _pending_futures_lock,
    ensure_waker_registered,
)
from ._pyroxide import get_status, wait_status

__all__ = ["TaskHandle"]

for _waker_callable in (ensure_waker_registered, _cleanup_waker):
    _waker_callable.__module__ = __name__
del _waker_callable


class TaskHandle:
    def __init__(self, task_id: int) -> None:
        self.task_id: int = task_id
        self._consumed: bool = False

    def __repr__(self) -> str:
        return f"<TaskHandle id={self.task_id}>"

    @property
    def status(self) -> str:
        """Queries the current status from the Rust Slab."""
        return get_status(self.task_id)

    def cancel(self) -> bool:
        """
        Attempts to prevent or terminate task execution.

        Pending tasks can be cancelled. Running isolated tasks can be
        terminated. Running in-process Python, native, and WASM tasks cannot
        be interrupted safely, so cancellation returns False and their real
        result remains available.
        """
        from ._pyroxide import cancel_task

        return cancel_task(self.task_id)

    def wait(
        self, poll_interval_ms: int = 10, timeout_sec: Optional[float] = None
    ) -> str:
        """
        Blocks the Python runtime until the background Rust worker completes the task.
        Uses native Rust condvar signal to sleep with 0% CPU usage.
        """
        if timeout_sec is not None:
            if timeout_sec < 0:
                raise ValueError("timeout_sec must be non-negative")
            timeout_ms: Optional[int] = int(timeout_sec * 1000)
        else:
            timeout_ms = None
        current_status: str = wait_status(self.task_id, timeout_ms)

        if current_status == "Cancelled":
            raise RuntimeError("Task cancelled")

        if timeout_sec is not None and current_status not in ("Completed", "Failed"):
            raise TimeoutError(f"Task {self.task_id} timed out.")

        return current_status

    def result(self, timeout_sec: Optional[float] = None, consume: bool = True) -> Any:
        """
        Blocks until the task is complete, then returns the result.
        If the task failed, raises the exception encountered.

        Args:
            timeout_sec: Maximum time in seconds to wait.
            consume: If True, automatically evicts the task from the Rust Slab once retrieved.
        """
        self.wait(timeout_sec=timeout_sec)
        from ._pyroxide import free_task, get_result

        res = get_result(self.task_id)
        if consume:
            free_task(self.task_id)
            self._consumed = True
        return res

    async def result_async(
        self, timeout_sec: Optional[float] = None, consume: bool = True
    ) -> Any:
        """
        Asynchronously awaits the task result, yielding control to the event loop.

        A task may have only one active asynchronous waiter. A second concurrent
        call raises ``RuntimeError``.
        """
        with _pending_futures_lock:
            if self.task_id in _async_waiters:
                raise RuntimeError(f"Task {self.task_id} is already being awaited")
            _async_waiters.add(self.task_id)

        fut: Optional[asyncio.Future] = None
        try:
            if sys.platform == "win32":
                current_status = self.status
                if current_status in ("Completed", "Failed", "Cancelled"):
                    return self.result(timeout_sec=0, consume=consume)
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.wait, 10, timeout_sec)
                return self.result(timeout_sec=0, consume=consume)

            loop = asyncio.get_running_loop()
            ensure_waker_registered(loop)

            fut = loop.create_future()
            with _pending_futures_lock:
                _pending_futures[self.task_id] = fut

            current_status = self.status
            if current_status in ("Completed", "Failed", "Cancelled"):
                return self.result(timeout_sec=0, consume=consume)

            try:
                if timeout_sec is not None:
                    await asyncio.wait_for(fut, timeout=timeout_sec)
                else:
                    await fut
            except asyncio.TimeoutError:
                raise TimeoutError(f"Task {self.task_id} timed out.")

            return self.result(timeout_sec=0, consume=consume)
        finally:
            with _pending_futures_lock:
                if fut is not None and _pending_futures.get(self.task_id) is fut:
                    _pending_futures.pop(self.task_id, None)
                _async_waiters.discard(self.task_id)

    def close(self) -> None:
        """
        Explicitly releases and frees the task memory in the Rust Slab.
        """
        if getattr(self, "_consumed", False):
            return
        try:
            current_status = self.status
            if current_status in ("Completed", "Failed", "Cancelled"):
                from ._pyroxide import free_task

                free_task(self.task_id)
            else:
                from ._pyroxide import set_autofree

                set_autofree(self.task_id)
            self._consumed = True
        except Exception:
            pass

    def __enter__(self) -> "TaskHandle":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        """
        Garbage collection destructor.
        Automatically frees the task memory in the Rust Slab when the Python handle is deleted/dropped.
        """
        self.close()
