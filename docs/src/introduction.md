# Introduction

Pyroxide (`pyro3`) is a lightweight, ultra-high-performance background task broker for Python, implemented in Rust via PyO3. 

It solves the problem of Python's **Global Interpreter Lock (GIL)** blocking multi-core task concurrency in heavily concurrent environments.

---

## High-Level Architecture

Pyroxide coordinates the main Python thread and background OS worker threads using a lock-free task engine:

```text
  [ Python Main Thread ]
            |
            |   (submit task / batch)
            v
  +-----------------------------------+
  |           Rust Broker             |
  |  - Bounded crossbeam channel      |
  |  - Thread-safe Slab Allocator     | <--- Read/Write lock protection
  +-----------------------------------+
            |
            |   (channel queue event)
            v
  +-----------------------------------+
  |       Worker Thread Pool          |
  |  - std::panic::catch_unwind       |
  |  - GIL-free native execution      |
  |  - Thread sleep cancellation      |
  +-----------------------------------+
            |
            |   (condvar signal completed)
            v
  [ TaskHandle.result() / result_async() ]
```

### Core Architecture Components

1. **Thread-Safe Slab Allocator:**
   Tasks are assigned IDs and held in a pre-allocated Rust `Slab` protected by a read-write lock (`RwLock`). This allows fast ID-based status queries and result retrieval without duplicating Python object data.
   
2. **Bounded Crossbeam Channel:**
   Worker task dispatch is coordinated via a bounded channel (`crossbeam_channel::bounded(10000)`). If the task queue fills up, Python submission threads block natively without holding the GIL, providing robust backpressure.

3. **Panic-Safe Workers:**
   Background tasks are executed within worker loops wrapped in `std::panic::catch_unwind`. If a task causes a Rust panic, the panic is caught and isolated. The thread is preserved to process remaining tasks, and the failure status is returned gracefully.

4. **GIL-Free Execution:**
   Tasks submitted via `@wasm_task` (WebAssembly sandbox) or `@dylib_task` (dynamic shared library) are executed on background threads without acquiring the Python GIL. This allows fully parallel multi-core CPU utilization (ideal for parsing, calculations, or IO-heavy operations).

---

## Alternative Solutions at a Glance

| Feature / Metric | Pyroxide | Threading (std) | Multiprocessing | Celery / RQ | Raw PyO3 Extension |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **GIL Bypass** | **✅ Yes** (WASM/dylib) | ❌ No | ✅ Yes | ✅ Yes | ✅ Yes |
| **IPC / Serialization** | **✅ None** (Shared Memory) | ✅ None | ❌ High (Pickling) | ❌ High (Network/Redis) | ⚠️ Medium (C-API boundary) |
| **Infrastructure** | **✅ None** (Embedded) | ✅ None | ⚠️ Low (Spawns processes) | ❌ High (Redis/RabbitMQ) | ⚠️ Medium (Rebuild required) |
| **Best For** | **🔥 High-perf in-process pipelines** | I/O-bound Python | CPU-heavy Python | Distributed tasks | Fixed static bindings |

For a detailed analysis of when to use Pyroxide vs. other libraries, see the [Library Comparison](comparison.md) page.
