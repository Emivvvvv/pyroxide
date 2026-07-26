# Introduction

Pyroxide (`pyro3`) is a lightweight, ultra-high-performance background task broker for Python, implemented in Rust via PyO3. 

It solves the problem of Python's **Global Interpreter Lock (GIL)** blocking multi-core task concurrency, while seamlessly leveraging **Python 3.14+ Free-Threaded (PEP 703)** builds when available.

---

## High-Level Architecture

Pyroxide coordinates task dispatch using a lock-free Rust engine with three optimized execution tiers:

```text
                     [ Python Main Thread ]
                               |
                               | (submit task / batch)
                               v
                     +-------------------+
                     |    Rust Broker    |
                     |  - Slab Allocator |
                     |  - Bounded Queue  |
                     +-------------------+
                               |
       +-----------------------+-----------------------+
       |                       |                       |
       v                       v                       v
 [ Tier 1: In-Process ]  [ Tier 2: Sandbox ]    [ Tier 3: Subprocess ]
  - Python 3.14+ Free-    - WASM JIT (wasmtime)  - Zero-Copy SHM (/dev/shm)
    Threaded (PEP 703)    - Compiled C/Rust/Zig  - Process crash safety
  - Lock-free Threads       Native Dynlibs       - Pre-warmed daemons
```

### Core Architecture Components

1. **Python 3.14+ Free-Threaded Engine (Tier 1):**
   Automatically detects `sys._is_gil_enabled()` (PEP 703). On free-threaded CPython builds, in-process Rust worker threads execute pure Python callables across all CPU cores simultaneously with sub-5-microsecond latency.

2. **Sandboxed & Native Engine (Tier 2):**
   Tasks submitted via `@wasm_task` (WebAssembly sandbox) or `@dylib_task` (dynamic shared library) execute on background threads without acquiring the Python GIL, running compiled machine code or JIT bytecode at native speed.

3. **Zero-Copy Subprocess Engine (Tier 3):**
   When `isolated=True` is enabled, Pyroxide dispatches tasks to pre-warmed worker daemons. Payloads $\ge 1\text{MB}$ are routed through Shared Memory (`/dev/shm`) for zero-copy memory transport and process crash isolation.

4. **Thread-Safe Slab Allocator & Bounded Channel:**
   Tasks are assigned atomic IDs in a lock-free `Slab` allocator. Worker dispatch is coordinated via `crossbeam_channel::bounded(10000)`, offering native backpressure without holding the GIL.

---

## Alternative Solutions at a Glance

| Feature / Metric | Pyroxide | Threading (std) | Multiprocessing | Celery / RQ | Raw PyO3 Extension |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **GIL Bypass / PEP 703** | **✅ Yes** (Auto 3.14t/WASM/dylib) | ❌ No | ✅ Yes | ✅ Yes | ✅ Yes |
| **IPC / Serialization** | **✅ None** (Shared Memory / In-Proc) | ✅ None | ❌ High (Pickling) | ❌ High (Network/Redis) | ⚠️ Medium (C-API boundary) |
| **Infrastructure** | **✅ None** (Embedded) | ✅ None | ⚠️ Low (Spawns processes) | ❌ High (Redis/RabbitMQ) | ⚠️ Medium (Rebuild required) |
| **Best For** | **🔥 High-perf in-process pipelines** | I/O-bound Python | CPU-heavy Python | Distributed tasks | Fixed static bindings |

For a detailed analysis of when to use Pyroxide vs. other libraries, see the [Library Comparison](comparison.md) page.
