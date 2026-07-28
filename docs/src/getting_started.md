# Getting started

## Submit a Python callable

```python
from pyroxide import task

@task
def factorial(value: int) -> int:
    import math
    return math.factorial(value)

handle = factorial(500)
print(handle.status)       # Pending, Running, or Completed
print(handle.result())     # waits and consumes the result
```

The decorated function accepts one payload argument. Submission returns a
`TaskHandle` immediately.

## Wait, consume, and close

- `handle.wait(timeout_sec=None)` waits and returns the terminal status.
- `handle.result(timeout_sec=None, consume=True)` returns or raises the result.
- `handle.result_async(...)` is the non-blocking asyncio form.
- `handle.close()` releases a terminal record or marks a running record for
  automatic release.

After a consuming `result()` or `close()`, do not query the handle again.

```python
with factorial(500) as handle:
    result = handle.result()
```

## Status values

| Status | Meaning |
| --- | --- |
| `Pending` | Accepted but not started |
| `Running` | A worker started it |
| `Completed` | Result is available |
| `Failed` | Execution raised or trapped |
| `Cancelled` | Work was cancelled before completion |

Running in-process work is not forcibly cancelled. See [Cancellation](cancellation.md).

## Process-isolated Python

```python
@task(isolated=True)
def cpu_work(value: int) -> int:
    return sum(i * i for i in range(value))
```

The callable and payload must be pickleable and importable by the worker. Define
the function in an importable module. Do not rely on closures, lambdas, or a
definition that exists only in `__main__`.

## Batch and group

```python
from pyroxide import group

handles = factorial.batch([10, 20, 30])
task_group = group(handles)
results = task_group.result()
```

Batch admission is all-or-nothing. Results preserve input order.

## Shut down cleanly

```python
import pyroxide

pyroxide.shutdown(wait=True, cancel_pending=False)
```

Shutdown is irreversible for the process. The default drains accepted work.
