import time
from ._pyroxide import get_status

class TaskHandle:
    def __init__(self, task_id: int):
        self.task_id = task_id

    @property
    def status(self) -> str:
        """Queries the current status from the Rust Slab."""
        return get_status(self.task_id)

    def wait(self, poll_interval_ms: int = 10, timeout_sec: float = None) -> str:
        """
        Blocks the Python runtime until the background Rust worker completes the task.
        """
        start_time = time.time()
        interval = poll_interval_ms / 1000.0

        while True:
            current_status = self.status
            if current_status in ("Completed", "Failed"):
                return current_status

            if timeout_sec and (time.time() - start_time) > timeout_sec:
                raise TimeoutError(f"Task {self.task_id} timed out.")

            time.sleep(interval)