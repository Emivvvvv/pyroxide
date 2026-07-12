# Isolated Worker Processes

By default, Pyroxide runs tasks in background OS threads. In v0.4.0, you can use `isolated=True` to run tasks in isolated OS processes.

Why use isolated processes?

1.  **Crash Safety:** If an unstable C extension or dynamic library triggers a Segmentation Fault (SIGSEGV), it only kills the worker process. The main Python app survives, and the task handle returns a `RuntimeError`.
2.  **True Python GIL Bypass:** Pure Python `@task`s normally hold the GIL. With `isolated=True`, the task runs in a separate Python interpreter process, bypassing the GIL completely.

## Usage

Pass `isolated=True` to any Pyroxide decorator:

```python
from pyroxide import task, wasm_task, dylib_task

# 1. Pure Python (GIL bypass & crash safety)
@task(isolated=True)
def heavy_computation(data: list) -> list:
    return [x * 2 for x in data]

# 2. WASM
@wasm_task("my_module", "process_data", isolated=True)
def process_wasm(data: str) -> str:
    pass

# 3. Dynamic Library
@dylib_task("unsafe_c_plugin", isolated=True)
def process_unsafe_c(data: bytes) -> bytes:
    pass

# Same API as in-process tasks
handle = heavy_computation([1, 2, 3])
print(handle.result())
```

## Internals

1.  **Warm Worker Pool:** Pyroxide pre-spawns worker processes. There is no `subprocess.Popen` startup cost during task execution.
2.  **IPC:** Communication uses Unix Domain Sockets (local TCP on Windows) with a custom binary framing protocol. It avoids `pickle` overhead for raw bytes and memoryviews.
3.  **Lifecycle:** Workers are single-threaded. When a task completes, the worker is reused. If a worker crashes, Pyroxide drops it and spawns a replacement.

## When to use `isolated=True`

| Scenario | Recommendation |
| :--- | :--- |
| **I/O-Bound Python** | `isolated=False`. Threads are fine. |
| **CPU-Bound Python** | `isolated=True`. Threads block the GIL, processes don't. |
| **WASM** | `isolated=False`. WASM is already sandboxed and GIL-free. |
| **Stable Native Code** | `isolated=False`. Threads are faster. |
| **Unstable Native Code** | `isolated=True`. Isolates segfaults. |

## Limitations

-   **Memory Copying:** `isolated=True` requires copying memory over IPC. For massive payloads (>100MB), `isolated=False` (which supports zero-copy) is much faster.
-   **Platform:** Optimized for Unix (Linux, macOS) via Domain Sockets. Windows falls back to local TCP.
