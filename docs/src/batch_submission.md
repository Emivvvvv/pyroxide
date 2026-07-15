# Batch Submission

When submitting a massive bulk of tasks concurrently, acquiring and releasing Pyroxide's internal read/write lock for every single task can lead to lock contention and overhead.

Pyroxide provides a bulk task submission API to optimize high-churn workloads.

## The `.batch()` Helper

Exposed on all `@task` decorated functions, `.batch()` acquires the Rust task slab write lock **exactly once** to register the entire collection of payloads:

```python
from pyroxide import task

@task
def calculate_square(x: int) -> int:
    return x * x

payloads = list(range(1000))

# Submits 1,000 tasks under a single lock acquisition
handles = calculate_square.batch(payloads)

# Retrieve results sequentially
results = [h.result() for h in handles]
```

### Performance Benefits

Acquiring the lock once for the entire batch avoids overhead and lock starvation:
*   Reduces submission overhead to **8 microseconds** per task.
*   Achieves up to a **2x latency reduction** compared to individual task loops.
*   Ideal for bulk imports, batch transactions (e.g. Odoo invoice reconciliations), and parallel parameter sweeps.

---

## Parallel Task Groups: `group()`

For managing multiple task handles as a single logical execution block, Pyroxide provides the `group()` helper. It wraps task handles into a `TaskGroup`, allowing developers to query status, await, or cancel all grouped tasks as a single unit:

```python
from pyroxide import task, group

@task
def calculate_square(x: int) -> int:
    return x * x

payloads = [10, 20, 30, 40]
handles = calculate_square.batch(payloads)

# Bundle handles into a TaskGroup
tg = group(handles)
print(tg.status) # "Running"

# Await all tasks and retrieve their results in order
# Pass consume=False to retain metadata if you need to check tg.status afterward
results = tg.result(consume=False) 
print(results)     # [100, 400, 900, 1600]
print(tg.status)   # "Completed"
```

### Async Context Manager (`async with`)

`TaskGroup` fully supports asynchronous context managers, aligning with standard `asyncio.TaskGroup` behavior. Entering the context manager allows you to group tasks, and exiting it automatically awaits all results. If an exception occurs in any task or inside the block, pending tasks are cancelled, and all task exceptions are collected and raised as an `ExceptionGroup` (requires Python 3.11+).

```python
from pyroxide import task, group
import asyncio

@task
def calculate_square(x: int) -> int:
    return x * x

async def main():
    payloads = [10, 20, 30, 40]
    handles = calculate_square.batch(payloads)
    
    try:
        async with group(handles) as tg:
            pass # Exiting the block will await all tasks concurrently
        
        # Results are preserved safely on the handles
        results = [h.result(consume=True) for h in tg.handles]
        print(results)
    except ExceptionGroup as eg:
        print("One or more tasks failed:", eg)

asyncio.run(main())
```

### TaskGroup Methods

A `TaskGroup` exposes the following API:
*   `tg.status`: Returns consolidated status (`"Running"`, `"Completed"`, `"Cancelled"`, `"Failed"`). If any task in the group has failed, the group status is `"Failed"`.
*   `tg.wait()`: Blocks until all tasks in the group are completed.
*   `tg.result(consume=True)`: Awaits all tasks and returns their results. If `consume=True` (default), task slots are immediately evicted from memory after reading to optimize slab capacity.
*   `tg.cancel()`: Triggers cancellation for all tasks in the group. Returns `True` if all tasks were successfully cancelled.
