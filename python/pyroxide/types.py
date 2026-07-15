from typing import Optional, Any
import os
import sys
import asyncio
from ._pyroxide import get_status, wait_status

# Global variables for async waker
_waker_r: Optional[int] = None
_waker_w: Optional[int] = None
_pending_futures: dict[int, asyncio.Future] = {}
_waker_registered: bool = False
_last_registered_loop: Optional[asyncio.AbstractEventLoop] = None


_waker_buffer: bytearray = bytearray()

def _waker_callback() -> None:
    global _waker_r, _pending_futures, _waker_buffer
    if _waker_r is None:
        return
    try:
        import struct
        data = os.read(_waker_r, 4096)
        _waker_buffer.extend(data)
        
        finished_task_ids = []
        while len(_waker_buffer) >= 8:
            chunk = _waker_buffer[:8]
            del _waker_buffer[:8]
            task_id = struct.unpack("<Q", chunk)[0]
            finished_task_ids.append(task_id)

        for task_id in finished_task_ids:
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
    except Exception:
        pass


def ensure_waker_registered(loop: asyncio.AbstractEventLoop) -> None:
    global _waker_r, _waker_w, _waker_registered, _last_registered_loop
    if sys.platform == "win32":
        return

    if _last_registered_loop is not None and _last_registered_loop != loop:
        # Loop changed, remove reader from old loop
        try:
            if _waker_r is not None:
                _last_registered_loop.remove_reader(_waker_r)
        except Exception:
            pass
        _waker_registered = False

    if not _waker_registered:
        try:
            from ._pyroxide import register_async_waker

            if _waker_r is None:
                _waker_r, _waker_w = os.pipe()
                os.set_blocking(_waker_r, False)
                register_async_waker(_waker_w)

            loop.add_reader(_waker_r, _waker_callback)
            _waker_registered = True
            _last_registered_loop = loop
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
        timeout_ms: Optional[int] = (
            int(timeout_sec * 1000) if timeout_sec is not None else None
        )
        current_status: str = wait_status(self.task_id, timeout_ms)

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
