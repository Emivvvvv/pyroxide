from typing import Optional, Any
import os
import sys
import asyncio
from ._pyroxide import get_status, wait_status

import struct
import threading
import time

# Global variables for async waker
_waker_r: Optional[int] = None
_waker_w: Optional[int] = None
_pending_futures: dict[int, asyncio.Future] = {}
_waker_thread: Optional[threading.Thread] = None

def _resolve_future_safe(task_id: int) -> None:
    fut = _pending_futures.get(task_id)
    if fut is not None and not fut.done():
        try:
            current_status = get_status(task_id)
            if current_status in ("Completed", "Failed", "Cancelled"):
                fut.set_result(current_status)
                _pending_futures.pop(task_id, None)
        except Exception as e:
            fut.set_exception(e)
            _pending_futures.pop(task_id, None)

def _waker_thread_loop() -> None:
    global _waker_r, _pending_futures
    buffer = bytearray()
    while True:
        try:
            if _waker_r is None:
                break
            data = os.read(_waker_r, 4096)
            if not data:
                break
            buffer.extend(data)
            while len(buffer) >= 8:
                chunk = buffer[:8]
                del buffer[:8]
                task_id = struct.unpack("<Q", chunk)[0]
                fut = _pending_futures.get(task_id)
                if fut is not None:
                    try:
                        loop = fut.get_loop()
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(_resolve_future_safe, task_id)
                    except Exception:
                        pass
        except Exception:
            time.sleep(0.01)

def ensure_waker_registered(loop: asyncio.AbstractEventLoop) -> None:
    global _waker_r, _waker_w, _waker_thread
    if sys.platform == "win32":
        return

    if _waker_thread is None:
        try:
            from ._pyroxide import register_async_waker

            _waker_r, _waker_w = os.pipe()
            register_async_waker(_waker_w)

            _waker_thread = threading.Thread(target=_waker_thread_loop, daemon=True)
            _waker_thread.start()
        except Exception:
            pass


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
        Attempts to cancel the task. Returns True if successfully cancelled,
        False if the task is already finished or cancelled.
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
        from ._pyroxide import get_result, free_task

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
        """
        if sys.platform == "win32":
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.wait, 10, timeout_sec)
            return self.result(timeout_sec=0, consume=consume)

        loop = asyncio.get_running_loop()
        ensure_waker_registered(loop)

        # Check if the task is already finished
        current_status = self.status
        if current_status in ("Completed", "Failed", "Cancelled"):
            return self.result(timeout_sec=0, consume=consume)

        # Create a future for this task
        fut = loop.create_future()
        _pending_futures[self.task_id] = fut

        try:
            if timeout_sec is not None:
                await asyncio.wait_for(fut, timeout=timeout_sec)
            else:
                await fut
        except asyncio.TimeoutError:
            _pending_futures.pop(self.task_id, None)
            raise TimeoutError(f"Task {self.task_id} timed out.")
        except Exception:
            _pending_futures.pop(self.task_id, None)
            raise
        finally:
            _pending_futures.pop(self.task_id, None)

        return self.result(timeout_sec=0, consume=consume)

    def __del__(self) -> None:
        """
        Garbage collection destructor.
        Automatically frees the task memory in the Rust Slab when the Python handle is deleted/dropped.
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
