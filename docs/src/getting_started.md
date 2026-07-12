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

## 2. GIL-Free Execution (WebAssembly & Dynamic Libraries)

For heavy compute tasks where you want to bypass the GIL completely:

```python
# Option A: Sandboxed WebAssembly
from pyroxide import register_wasm, wasm_task

with open("my_module.wasm", "rb") as f:
    register_wasm("my_module", f.read())

@wasm_task("my_module")
def compute(payload: str) -> str:
    pass

# Option B: Dynamic shared library (compiled on-the-fly)
from pyroxide import compile_dylib, dylib_task

compile_dylib("my_lib", RUST_SOURCE_CODE)

@dylib_task("my_lib")
def process(payload: str) -> str:
    pass
```

## 3. Querying Task Status

The `TaskHandle` provides the `.status` property to track execution:

*   `Pending`: Stored in the Slab, queued for execution.
*   `Running`: Currently being processed by a worker thread.
*   `Completed`: Finished successfully; results are ready to retrieve.
*   `Failed`: Stopped due to panic or exception.
*   `Cancelled`: Explicitly cancelled before or during execution.
