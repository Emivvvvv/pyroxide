from typing import Optional, Any
from ._pyroxide import get_status, wait_status


class TaskHandle:
    def __init__(self, task_id: int) -> None:
        self.task_id: int = task_id

    @property
    def status(self) -> str:
        """Queries the current status from the Rust Slab."""
        return get_status(self.task_id)

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
        return res

    def __del__(self) -> None:
        """
        Garbage collection destructor.
        Automatically frees the task memory in the Rust Slab when the Python handle is deleted/dropped.
        """
        try:
            from ._pyroxide import free_task

            free_task(self.task_id)
        except Exception:
            pass
