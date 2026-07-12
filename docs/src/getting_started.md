# Getting Started

Offloading computational workloads to Pyroxide's Rust-backed thread pool is as simple as decorating your functions.

## 1. Offloading Python Callables

By default, the `@task` decorator schedules your function to be run on background threads in the Rust pool. 

```python
from pyroxide import task

@task
def calculate_factorial(n: int) -> int:
    import math
    return math.factorial(n)

# Submit immediately (non-blocking)
handle = calculate_factorial(500)
print(f"Task status: {handle.status}")  # "Pending" or "Running"

# Wait and retrieve the result (blocking via condvar)
result = handle.result()
print(f"Factorial result: {result}")
```

## 2. Offloading Native Rust Tasks (GIL-Free)

For heavy compute tasks where you want to bypass the Global Interpreter Lock (GIL) completely and execute code natively:

```python
from pyroxide import task

# Decorating with native=True offloads execution to Rust core
@task(native=True)
def parse_and_process(payload: str) -> None:
    pass

# Processes the string completely GIL-free inside Rust
handle = parse_and_process("SLEEP:100")
result = handle.result()
```

## 3. Querying Task Status

The `TaskHandle` provides the `.status` property to track execution:

*   `Pending`: Stored in the Slab, queued for execution.
*   `Running`: Currently being processed by a worker thread.
*   `Completed`: Finished successfully; results are ready to retrieve.
*   `Failed`: Stopped due to panic or exception.
*   `Cancelled`: Explicitly cancelled before or during execution.
