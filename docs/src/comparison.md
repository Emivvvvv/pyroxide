# Library & Ecosystem Comparison Guide

Choosing the right concurrency model or task broker is critical for your application's performance, stability, and developer velocity. This guide compares Pyroxide with other common Python concurrency patterns and native binding ecosystems, backed by empirical measurements detailed in the [Performance & Benchmarks](benchmarks.md) chapter.

---

## Architectural Taxonomy

To make fair and accurate comparisons, concurrency solutions should be grouped into their proper architectural tiers:

| Architectural Tier | System Characteristics | Representative Libraries | Latency Profile | Best For |
| :--- | :--- | :--- | :---: | :--- |
| **In-Process Micro-Brokers** | Single host, shared process memory, OS thread pool | **Pyroxide (`@task`)**, `ThreadPoolExecutor` | **Microseconds** ($\sim 25\mu\text{s}$) | Fast in-process task offloading & asyncio non-blocking tasks |
| **Process-Isolated Pools** | Multi-process on single host, IPC / SHM transport | **Pyroxide (`isolated=True`)**, `multiprocessing.Pool`, `loky` | **Sub-milliseconds** ($\sim 120\mu\text{s}$) | Crash-isolated CPU tasks & Python GIL bypass |
| **Compiled Native Task Engines** | Native dynamic compilation, lock-free threads, WASM VM | **Pyroxide (`@dylib_task` / `@wasm_task`)**, PyO3+Rayon, nanobind+OpenMP | **Microseconds** ($\sim 3.8\mu\text{s}$) | Maximum CPU speedup, numerical calculations & sandboxing |
| **Distributed Task Queues** | Multi-node, TCP network broker, persistent database | Celery, RQ, Dramatiq, Temporal | **Milliseconds** ($\sim 5\text{ms} - 12\text{ms}$) | Cross-server scaling, network retries & durable jobs |

---

## 1. Pyroxide vs. Python 3.14 Free-Threaded CPython (PEP 703)

Python 3.13 introduced experimental free-threaded builds (`--disable-gil`, PEP 703), and Python 3.14 continues to mature free-threaded execution. Free-threaded CPython allows standard Python threads (`threading.Thread` or `ThreadPoolExecutor`) to run on multiple CPU cores simultaneously without holding a global interpreter lock.

### Auto-Detection & Free-Threaded Engine Support (v0.10.0)
Pyroxide automatically detects free-threaded CPython builds using `pyroxide.config.is_free_threaded()` (checking `sys._is_gil_enabled()`). 
* On **Python 3.14+ Free-Threaded CPython**, `@task` routes pure Python callables to Pyroxide's lock-free threadpool with true multi-core parallel execution and sub-5-microsecond latency.
* On **Standard GIL-Bound CPython**, setting `isolated=True` routes tasks to zero-copy shared memory (/dev/shm) worker processes, while `@dylib_task` / `@wasm_task` execute compiled machine code or WASM JIT without holding the GIL.

---

## 2. Pyroxide vs. Python `multiprocessing` & `loky`

Python's built-in `multiprocessing` module (and `ProcessPoolExecutor`) runs tasks in separate Python interpreter processes to bypass the GIL. `loky` (used by scikit-learn) improves worker process lifecycle management and pickling robustness.

### Comparison
* **Memory & Startup Overhead**: `multiprocessing` spawns fresh Python processes on demand, duplicating interpreter memory and increasing startup latency. **Pyroxide** maintains pre-warmed worker processes (`isolated=True`) with low idle memory footprints.
* **Zero-Copy Shared Memory**: Standard `multiprocessing` serializes data via `pickle` over OS pipes. For payloads $\ge 1\text{MB}$, this causes severe serialization bottlenecks. **Pyroxide** automatically routes payloads $\ge 1\text{MB}$ over OS Shared Memory (`/dev/shm`) with zero-copy deserialization.

### Empirical Comparison Matrix (100 Tasks - Fibonacci 20 Workload)

| Concurrency Strategy | Execution Time (100 Tasks) | Speedup vs std ThreadPool | Architecture Tier | GIL Status |
| :--- | :---: | :---: | :--- | :---: |
| **Pyroxide `@dylib_task` (C Native)** | **`0.0038 s`** | **🔥 23.1x speedup** | **Native Dynamic Plugin** | **Bypassed (C-ABI)** |
| **Python 3.14t Free-Threaded (PEP 703)** | **`0.0168 s`** | **🚀 5.2x speedup** | **Free-Threaded CPython** | **Disabled (`3.14t`)** |
| **Pyroxide `@task(isolated=True)`** | **`0.1292 s`** | **⚡ 0.7x (1.94x faster than Loky)** | **Zero-Copy SHM Process Pool** | **Bypassed (Subprocess)** |
| **ThreadPoolExecutor (CPython 3.11)** | `0.0881 s` | `1.0x (baseline)` | Standard Threading | Locked (GIL) |
| **Loky Process Pool (Joblib)** | `0.2509 s` | `0.35x` | Subprocess Pool | Bypassed (Subprocess) |
| **ProcessPoolExecutor (Multiprocessing)** | `2.7964 s` | `0.03x` | Pickled Subprocess Pipes | Bypassed (Subprocess) |

### Key Empirical Findings
1. **Python 3.14t Free-Threaded Performance**: Under Python 3.14t (`GIL_disabled=True`), pure-Python `ThreadPoolExecutor` achieves `0.0168s` (a **5.2x speedup** over GIL-bound CPython 3.11), proving that PEP 703 enables true multi-core parallel execution for pure-Python tasks.
2. **Why Pyroxide Dynamic Plugins Beat Free-Threaded Python**: Pyroxide `@dylib_task` executes in `0.0038s`—**4.4x faster than Python 3.14t Free-Threaded**. While free-threading removes the GIL, pure Python bytecode execution remains interpreted, whereas Pyroxide combines lock-free thread dispatching with native machine code.
3. **Process Isolation Benchmarks**: Pyroxide `@task(isolated=True)` (`0.1292s`) outperforms `loky` (`0.2509s`, **1.94x faster**) and `ProcessPoolExecutor` (`2.7964s`, **21.6x faster**) due to pre-warmed worker daemons and zero-copy Shared Memory (`/dev/shm`) transport.

---

## 3. Pyroxide vs. Celery / RQ

Celery and RQ are distributed task queues designed to run jobs on separate worker machines across network boundaries.

### Comparison
* **Infrastructure Overhead**: Celery requires setting up a message broker (RabbitMQ/Redis) and running separate worker daemons. **Pyroxide** is embedded inside your Python application with zero external infrastructure dependencies.
* **Latency Profile**: Celery tasks incur TCP round-trips, broker serialization, and polling delay (**4.8 ms to 12.5 ms**). **Pyroxide** task dispatch signaling takes **25 microseconds (200x to 500x faster)** using OS-level condition variable signaling.

---

## 4. Pyroxide vs. Native Extension Pools (PyO3 + Rayon / C++ OpenMP / TBB)

Statically compiling C++ extensions (`nanobind`/`pybind11` + OpenMP) or Rust PyO3 modules (`PyO3 + Rayon`) is standard for high-performance Python libraries.

### Comparison
* **Dynamic On-the-Fly Compilation**: Building custom PyO3/Rayon modules requires setting up `maturin` build steps and generating platform wheels. Pyroxide dynamic plugins ([compile_rust](file:///Users/emivvvvv/Documents/GitHub/pyroxide/README.md#L158), `compile_c`, `compile_zig`) compile code strings directly at runtime with binary caching.
* **WASM Sandboxing**: Neither Rayon nor OpenMP provide memory sandboxing. Pyroxide's WASM engine ([@wasm_task](file:///Users/emivvvvv/Documents/GitHub/pyroxide/README.md#L134-L153)) allows executing untrusted multi-tenant plugins in a secure JIT sandbox with strict CPU and memory bounds.

