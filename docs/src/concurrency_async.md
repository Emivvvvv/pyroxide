# Concurrency and asyncio

Use `result_async()` when an asyncio application needs a Pyroxide result without
blocking its event loop. Calling `handle.result()` on the event-loop thread
blocks every other coroutine until the task finishes.

```python
import asyncio
from pyroxide import task

@task
def calculate(value: int) -> int:
    return sum(i * i for i in range(value))

async def main() -> None:
    handle = calculate(1_000_000)
    result = await handle.result_async(timeout_sec=5)
    print(result)

asyncio.run(main())
```

`result_async()` preserves the same result and exception behavior as `result()`.
Timeout only stops waiting; it does not cancel the task.

The same pattern works in an asynchronous web handler without coupling the task
to a web framework:

```python
async def calculate_route(value: int) -> dict[str, int]:
    result = await calculate(value).result_async(timeout_sec=5)
    return {"result": result}
```

## Completion notification

On Unix, Rust writes to a non-blocking completion pipe. A dedicated Python reader
thread scans registered futures and schedules completion on each owning event loop
with `call_soon_threadsafe`. It does not poll task status on a timer.

On Windows, Pyroxide waits on its native condition variable through asyncio's
default executor.

Applications may await different handles from different event loops. A task may
have only one active `result_async()` waiter; a second concurrent call raises
`RuntimeError`. A consuming result releases the task record.

See [Getting started](getting_started.md) for handle lifetime and
[Task cancellation](cancellation.md) for the difference between a wait timeout
and stopping work.
