# Isolated Worker Processes

By default, Pyroxide runs tasks in background OS threads. In v0.5.0, you can use `isolated=True` to run tasks in isolated OS processes with high-performance cross-platform IPC and Zero-Copy Shared Memory (SHM) routing.

Why use isolated processes?

1.  **Crash Safety:** If an unstable C extension or dynamic library triggers a Segmentation Fault (SIGSEGV), it only kills the worker process. The main Python app survives, and the task handle returns a `RuntimeError`.
2.  **True Python GIL Bypass:** Pure Python `@task`s normally hold the GIL. With `isolated=True`, the task runs in a separate Python interpreter process, bypassing the GIL completely.

## Usage

Pass `isolated=True` to any Pyroxide decorator:

```python
from pyroxide import task, wasm_task, dylib_task, load_wasm, load_dylib

# 1. Pure Python (GIL bypass & crash safety)
@task(isolated=True)
def heavy_computation(data: list) -> list:
    return [x * 2 for x in data]

# 2. WASM (Decorators)
@wasm_task("my_module", "process_data", isolated=True)
def process_wasm(data: str) -> str:
    pass

# 3. Dynamic Library (Decorators)
@dylib_task("unsafe_c_plugin", isolated=True)
def process_unsafe_c(data: bytes) -> bytes:
    pass

# 4. OOP Proxies (New in v0.6.0)
cipher = load_wasm("my_module", isolated=True)
crypto = load_dylib("unsafe_c_plugin", isolated=True)

# Same API as in-process tasks
handle = heavy_computation([1, 2, 3])
print(handle.result())

# Call dynamic symbols on isolated workers
handle_hash = crypto.hash_sha256(b"message")
print(handle_hash.result())
```

## Internals & Optimizations

1.  **Warm Worker Pool & Scale-to-Zero:** Pyroxide pre-spawns worker processes so there is no execution-time process startup latency. To minimize memory usage, an idle reaper thread automatically terminates idle worker processes. You can configure `PYROXIDE_MIN_WORKERS` (default: `0`) to keep a minimum number of warm workers alive and block-waiting on the socket to eliminate cold-start latency entirely, while any workers above this threshold are reaped when idle for longer than `PYROXIDE_IDLE_TIMEOUT_SEC` (default: `60` seconds).
2.  **Cross-Platform Local Sockets:** IPC uses Unix Domain Sockets on Linux/macOS and Named Pipes on Windows (backed by the `interprocess` crate), avoiding slow TCP loopback overhead.
3.  **Hybrid Zero-Copy Shared Memory (SHM):** For small payloads (< 1MB), data is sent directly over the local socket. For large payloads (>= 1MB), Pyroxide utilizes OS-level Shared Memory (`shared_memory` crate) for zero-copy transfers, avoiding serialization bottleneck.
4.  **Lifecycle:** Workers are single-threaded. When a task completes, the worker is reused. If a worker crashes, Pyroxide drops it and spawns a replacement.

## When to use `isolated=True`

| Scenario | Recommendation |
| :--- | :--- |
| **I/O-Bound Python** | `isolated=False`. Threads are fine. |
| **CPU-Bound Python** | `isolated=True`. Threads block the GIL, processes don't. |
| **WASM** | `isolated=False`. WASM is already sandboxed and GIL-free. |
| **Stable Native Code** | `isolated=False`. Threads are faster. |
| **Unstable Native Code** | `isolated=True`. Isolates segfaults. |
| **Massive Payloads (>=1MB)** | `isolated=True` with Hybrid SHM routing is fast, but `isolated=False` has no process transition overhead. |

## Limitations

-   **Memory Copying:** Although SHM routing provides zero-copy across the process boundary, there is still serialization/deserialization overhead for complex Python objects.
-   **Resource Isolation:** Ensure your system supports shared memory mapping (standard on modern macOS, Linux, and Windows). If SHM creation fails, Pyroxide gracefully falls back to socket transmission.

---

## SHM Leak Protection

For large payloads, Pyroxide maps data to OS-level Shared Memory (SHM). If a worker process crashes, panics, or terminates mid-execution, standard OS-level SHM files can leak, causing the host to run out of descriptors or memory.

To prevent this, Pyroxide implements strict **SHM Leak Protection**:
- **RAII Drop Guards**: Both the master and worker runtimes wrap shared memory segments in a custom Rust `ShmemGuard`.
- **Automatic Unmapping & Unlinking**: When the task finishes or if either the worker/master process crashes or panics mid-execution, Rust's panic unwinding automatically drops the guard, unmapping and unlinking the shared memory segment from the OS filesystem (POSIX `shm_unlink` on macOS/Linux).

## Orphan Process Mitigation

If the master Python process crashes unexpectedly or is killed (e.g. `SIGKILL`), running isolated worker processes could potentially become orphaned "zombie" processes that leak CPU and memory indefinitely. 

Pyroxide guarantees immediate worker termination on master crashes across all major platforms:
- **Linux**: Child processes are spawned with `libc::PR_SET_PDEATHSIG`, instructing the Linux kernel to instantly send `SIGKILL` to the child if the parent process dies.
- **macOS/Unix**: A lightweight background thread is injected into every worker process. It polls `libc::getppid()` every 500ms; if the parent PID changes to `1` (indicating the parent died and the process was adopted by `init`), the worker instantly terminates.
