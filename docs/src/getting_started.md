# Getting started

A Pyroxide task is a Python callable that returns a `TaskHandle` when you submit
it. The handle lets you inspect, wait for, await, cancel, or release that piece
of work.

## Decorate and submit

```python
from pyroxide import task

@task
def square(value: int) -> int:
    return value * value

handle = square(12)
print(handle.status)    # Pending, Running, or Completed
print(handle.result())  # 144
```

The decorated callable accepts one payload argument. Calling `square(12)` does
not run the function inline; it submits the payload and returns immediately.

`result()` waits for completion. By default it also consumes the task record, so
the handle should not be queried again.

## Wait without consuming

Use `wait()` when you need the terminal status before reading the result:

```python
handle = square(12)
status = handle.wait(timeout_sec=2)
result = handle.result()
```

`wait()` returns `Completed` or `Failed`. It raises `TimeoutError` if the
deadline expires and `RuntimeError` if the task was cancelled.

Use `consume=False` when more than one part of your code must inspect a finished
handle:

```python
handle = square(12)
result = handle.result(consume=False)
print(handle.status)
handle.close()
```

`close()` releases a terminal record. If work is still running, it marks the
record for automatic release after completion. A context manager does the same
cleanup:

```python
with square(12) as handle:
    result = handle.result()
```

## Await inside an event loop

Do not call blocking `result()` on an event-loop thread. Await the asynchronous
form:

```python
result = await square(12).result_async(timeout_sec=2)
```

The result and exception semantics match `result()`. A handle supports only one
active asynchronous waiter. See [Concurrency and asyncio](concurrency_async.md)
for a complete example.

## Understand status

| Status | Meaning |
| --- | --- |
| `Pending` | Accepted but not started |
| `Running` | A worker started it |
| `Completed` | A result is available |
| `Failed` | Execution raised or trapped |
| `Cancelled` | Work was cancelled before completion |

Cancellation depends on the execution boundary. Pending work can be cancelled;
running in-process work cannot be safely interrupted. Read
[Task cancellation](cancellation.md) before relying on it for control flow.

## Do more

- [Choose threads, processes, WASM, or native execution](execution_modes.md).
- [Submit related payloads as a batch](batch_submission.md).
- [Run CPU-bound Python in an isolated process](isolated_workers.md).
- [Prepare the engine for production](operations.md).

Shut Pyroxide down during application teardown:

```python
import pyroxide

pyroxide.shutdown(wait=True, cancel_pending=False)
```

Shutdown is idempotent and irreversible in the current process. The default
waits for accepted work to finish.
