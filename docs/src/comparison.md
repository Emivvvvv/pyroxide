# Library Comparison

Choosing the right concurrency model or task broker is critical for your application's performance, stability, and developer velocity. This guide outlines how Pyroxide compares with other common Python concurrency patterns.

---

## 1. Pyroxide vs. Python `multiprocessing`

Python's built-in `multiprocessing` module (and `ProcessPoolExecutor`) runs tasks in separate Python interpreter processes to bypass the Global Interpreter Lock (GIL).

### Comparison
*   **Memory Overhead**: `multiprocessing` forks or spawns brand new OS processes, which duplicate Python interpreter memory and increase startup latency. **Pyroxide** runs lightweight background OS threads in the same process with negligible overhead.
*   **IPC (Inter-Process Communication)**: Data sent to a subprocess must be serialized (using `pickle`) and sent over OS pipes/sockets. For large payloads (e.g. byte arrays or machine learning inputs), this overhead is massive. **Pyroxide** shares process memory directly; passing byte arrays or memoryviews is zero-copy.
*   **Task Management**: Subprocesses are slow to cancel and cannot gracefully catch panics without process death. **Pyroxide** uses atomic state slots and thread-safe signaling to cancellation.

### Decision Matrix
> [!TIP]
> **Use Python Multiprocessing when:**
> - You are executing pure Python code that is CPU-heavy.
> - You do not want to write or compile any compiled code (Rust, C, Zig).
> - You have simple payloads that pickle quickly.
>
> **Use Pyroxide when:**
> - You have large byte/string payloads and need zero-copy memory performance.
> - You want low-latency background offloading (microseconds instead of milliseconds).
> - You want to run Rust, C, Zig, or WebAssembly sandbox plugins GIL-free.

---

## 2. Pyroxide vs. Celery / RQ

Celery and RQ are distributed task queues designed to run jobs on separate worker machines.

### Comparison
*   **Complexity**: Celery requires setting up a message broker (like Redis or RabbitMQ) and running separate worker daemon processes. **Pyroxide** is embedded inside your Python process; it has **zero** infrastructure dependencies.
*   **Latency**: Celery tasks incur network round-trip times (submitting to broker, broker sending to worker, returning result). Latency is measured in milliseconds. **Pyroxide** task dispatch and completion signaling (`Condvar`) takes microseconds (0.02ms baseline overhead).

### Decision Matrix
> [!TIP]
> **Use Celery / RQ when:**
> - You need distributed, multi-server horizontal scaling.
> - You need persistence (saving tasks to disk in case the server crashes).
> - You need advanced workflows (task chains, chords, scheduling cron jobs).
>
> **Use Pyroxide when:**
> - You need high-performance, in-process background pipelining.
> - You want zero external dependencies (no Redis/RabbitMQ to configure or maintain).
> - Microsecond latency is required for high-frequency task offloading.

---

## 3. Pyroxide vs. Raw PyO3 / Maturin C-Extensions

Writing a custom Rust C-Extension using PyO3 is the standard way to speed up Python with Rust.

### Comparison
*   **Development Speed**: With raw PyO3, any change to your native code requires compiling a new wheel, rebuilding the Python environment, and re-deploying. **Pyroxide** compiles and loads dynamic code on-the-fly (`compile_dylib()`, `compile_c()`, `compile_zig()`), providing rapid iteration directly in Python script files.
*   **GIL Offloading**: PyO3 requires manually calling `py.allow_threads(...)` to release the GIL, and managing cross-thread safety. **Pyroxide** abstracts all thread dispatching, lock-free status monitoring, and GIL-release boundaries automatically under the hood.

### Decision Matrix
> [!TIP]
> **Use Raw PyO3 / Maturin when:**
> - You are shipping a statically packaged Python library to PyPI for other developers to use.
> - You need zero-copy sharing of complex data structures (like sharing `ndarray` objects using the buffer protocol).
>
> **Use Pyroxide when:**
> - You are building application code and want to rapidly prototype compiled native logic without complex build/distribution pipelines.
> - You want to run safe, sandboxed calculations using WebAssembly (`@wasm_task`).
