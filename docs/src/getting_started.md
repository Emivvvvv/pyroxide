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

For heavy compute tasks where you want to bypass the GIL completely, you can use either **decorators** (for single entry points) or **Object-Oriented Proxies** (for multi-function libraries/modules):

### Option A: Sandboxed WebAssembly
Run safe, sandboxed code at native speeds:

```python
from pyroxide import register_wasm, wasm_task, load_wasm

# Load WASM bytecode
with open("my_module.wasm", "rb") as f:
    register_wasm("my_module", f.read())

# 1. Access via decorator:
@wasm_task("my_module", "run")
def compute(payload: str) -> str:
    pass

handle = compute("data")

# 2. Or access via OOP Proxy (dynamic method dispatch):
cipher = load_wasm("my_module")
handle = cipher.run("data")
```

### Option B: Dynamic shared library (Compiled on-the-fly)
Load compiled native C, Rust, or Zig libraries:

```python
from pyroxide import compile_rust, dylib_task, load_dylib

compile_rust("my_lib", RUST_SOURCE_CODE)

# 1. Access via decorator:
@dylib_task("my_lib", "my_func")
def process(payload: str) -> str:
    pass

handle = process("data")

# 2. Or access via OOP Proxy:
my_lib = load_dylib("my_lib")
handle = my_lib.my_func("data")
```

---

## 3. IDE Autocomplete (Type Stubs)

When calling functions on OOP proxies, editors (like VS Code) won't show autocompletion because symbols are loaded at runtime. You can generate standard PEP 484 type stub files (`.pyi`) to get full IDE autocomplete and type-checking.

### Option A: Via Command Line (Recommended)
You can statically build stubs before running your code by scanning compile/register calls in your codebase:
```bash
pyroxide build-stubs --scan --scan-dir . --out-dir .
```

### Option B: Via Python Helper
```python
from pyroxide import generate_stubs

# Writes "my_lib_proxy.pyi" to your project directory
generate_stubs("my_lib", library_type="dylib")
```

## 4. Querying Task Status

The `TaskHandle` provides the `.status` property to track execution:

*   `Pending`: Stored in the Slab, queued for execution.
*   `Running`: Currently being processed by a worker thread.
*   `Completed`: Finished successfully; results are ready to retrieve.
*   `Failed`: Stopped due to panic or exception.
*   `Cancelled`: Explicitly cancelled before or during execution.

---

## 5. Environment Variable Reference

You can configure Pyroxide's runtime dynamically using the following environment variables:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `PYROXIDE_WORKERS` | Number of background worker threads in the Rust thread pool. | CPU core count |
| `PYROXIDE_SHM_THRESHOLD` | Payload size threshold in bytes above which data uses Shared Memory (SHM) instead of socket pipes. | `1048576` (1MB) |
| `PYROXIDE_WASM_TICK_MS` | Granularity of the WASM epoch deadline interruption loop in milliseconds. | `10` |
| `PYROXIDE_MAX_TASKS_PER_WORKER` | Maximum number of tasks an isolated worker runs before it is recycled to prevent memory leaks. | `100` |
| `PYROXIDE_WORKER_STARTUP_TIMEOUT_SEC` | Timeout in seconds for a new worker process to start up and connect. | `5` |
| `PYROXIDE_IDLE_TIMEOUT_SEC` | Idle time in seconds before an inactive isolated worker process is terminated. | `60` |
| `PYROXIDE_MIN_WORKERS` | Minimum number of warm worker processes to keep alive at all times. | `0` |
| `PYROXIDE_DISABLE_COMPILATION` | Set to `1` or `true` to disable runtime compilation of C/Zig/Rust plugins for strict security compliance. | Disabled |
