# Concurrency & Asyncio

When waiting for task results from asynchronous runtimes (such as FastAPI, Sanic, or standard `asyncio` applications), using the blocking `.result()` method will block Python's main thread and freeze the event loop.

To prevent this, Pyroxide provides the non-blocking asynchronous await API.

## Non-Blocking event loop awaiting (`result_async`)

By awaiting `result_async()`, you temporarily yield control back to Python's asyncio event loop, allowing it to process other concurrent requests while the Rust pool executes the task:

```python
import asyncio
from pyroxide import task

@task
def cpu_bound_task(x: int) -> int:
    return sum(i * i for i in range(x))

async def request_handler():
    handle = cpu_bound_task(10_000_000)
    
    # Non-blocking await
    result = await handle.result_async()
    print("Task result:", result)

async def main():
    # Runs the handler concurrently with other asyncio jobs
    await asyncio.gather(
        request_handler(),
        asyncio.sleep(0.1) # Event loop remains responsive!
    )

asyncio.run(main())
```

### Under the Hood

The `result_async` method offloads Pyroxide's native condvar blocking check to the asyncio event loop's default `ThreadPoolExecutor`. This keeps the main thread running the event loop while background OS threads wait for the completion signal from Rust.
