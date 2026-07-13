# Task Cancellation

Pyroxide supports task cancellation before execution begins or while a task is running.

## Cancelling a Task

Use the `.cancel()` method on `TaskHandle` to abort execution:

```python
import time
from pyroxide import task

@task
def long_running_report(n: int) -> int:
    # Simulates a long-running CPU-bound operation (e.g. a financial report)
    total = 0
    for i in range(n):
        total += i
    time.sleep(5)  # Simulates waiting for a slow external resource
    return total

# Submit a long-running task
handle = long_running_report(1_000_000)

# Abort the task before it finishes
cancelled = handle.cancel()
print(f"Cancelled: {cancelled}")  # Returns True if successfully aborted

# Assert status is Cancelled
print(f"Status: {handle.status}")  # "Cancelled"

try:
    handle.result()
except RuntimeError as e:
    # A cancelled task raises a RuntimeError on result query
    print("Caught error:", e)  # Output: Task cancelled
```

---

## Cancellation Internals

1. **Pre-Execution Check:**
   When a task is popped from the crossbeam queue, workers check the task's cancelled atomic flag. If `true`, execution is skipped immediately.
   
2. **Mid-Execution Sleep Aborting:**
   During native operations (like sleeps), workers split long pauses into 10ms intervals and query the cancelled flag periodically. If a cancel signal is received, the thread aborts immediately.

3. **Status Preservation:**
   Cancellation transitions the task status atomically to `Cancelled`. Workers respect this state and do not overwrite it with `Completed` or `Failed` upon finalization.
