# Concurrency and asyncio

Calling `handle.result()` inside an event-loop thread blocks that loop. Await
`result_async()` instead.

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

## Completion notification

On Unix, Rust writes to a non-blocking completion pipe. A dedicated Python reader
thread scans registered futures and schedules completion on each owning event loop
with `call_soon_threadsafe`. It does not poll task status on a timer.

On Windows, Pyroxide waits on its native condition variable through asyncio's
default executor.

Applications may await different handles from different event loops. A task may
have only one active `result_async()` waiter; a second concurrent call raises
`RuntimeError`. A consuming result releases the task record.
