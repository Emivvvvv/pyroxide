<!-- PROJECT LOGO -->
<br />
<div align="center">
  <a href="https://github.com/emivvvvv/pyroxide">
    <img src="https://raw.githubusercontent.com/emivvvvv/pyroxide/main/pyroxide.svg" alt="Logo" width="80" height="80">
  </a>

  <h3 align="center">Pyroxide</h3>

  <p align="center">
    A lock-free, high-concurrency background task broker for Python, powered by Rust.
    <br />
    <br />
    <a href="https://www.rust-lang.org/"><img src="https://img.shields.io/badge/rust-stable-brightgreen.svg" alt="Rust" style="display:inline;margin:0 2px;"></a>
    <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.8+-blue.svg" alt="Python" style="display:inline;margin:0 2px;"></a>
    <a href="LICENSE-MIT"><img src="https://img.shields.io/badge/License-MIT%2FApache--2.0%2FCoffee-blue.svg" alt="License: MIT/Apache-2.0/Coffee" style="display:inline;margin:0 2px;"></a>
    <br />
    <br />
    <a href="https://emivvvvv.github.io/pyroxide/"><strong>Explore the Docs »</strong></a>
    <br />
    <br />
    <a href="https://emivvvvv.github.io/pyroxide/api/pyroxide.html">API Reference</a>
    &middot;
    <a href="https://github.com/emivvvvv/pyroxide/tree/main/examples">See Examples</a>
    &middot;
    <a href="https://github.com/emivvvvv/pyroxide/issues/new?labels=bug">Report Bug</a>
    &middot;
    <a href="https://github.com/emivvvvv/pyroxide/issues/new?labels=enhancement">Request Feature</a>
  </p>
</div>

---

Pyroxide (`pyro3`) is a lightweight, ultra-high-performance background task broker designed to bridge Python and Rust. It allows CPU-bound or blocking workloads to bypass the Python Global Interpreter Lock (GIL) with minimal memory overhead and zero CPU-sleep polling.

## Why Pyroxide?

*   🚀 **Bypass the GIL (GIL-Free)**: Execute CPU-intensive compiled tasks on background OS threads without holding the Python GIL.
*   ⚡ **Microsecond Latency**: Utilizes OS-level signaling (`Condvar`) rather than CPU-burning thread polling, dispatching and completing tasks in under **25 microseconds**.
*   📦 **Zero Infrastructure**: Runs completely in-process. No Redis, RabbitMQ, or Celery worker daemons to configure or maintain.
*   💾 **Zero-Copy Serialization**: Pass large byte arrays, memoryviews, or columnar buffers across the C-ABI boundary without copy or `pickle` overhead.
*   🛠️ **On-the-Fly Native Compilers**: Write code as Python strings and compile them to dynamic libraries on-the-fly (**Rust**, **C**, and **Zig** supported!).

---

## Pyroxide vs. Alternatives

| Broker / Engine | GIL Bypass | IPC / Serialization Cost | Infrastructure | Best For |
| :--- | :--- | :--- | :--- | :--- |
| **Pyroxide** | **Yes** (WASM/dylib) | **None** (Shared memory) | **None** (Embedded) | In-process high-perf background pipelines |
| **Multiprocessing** | Yes | High (Pickling) | Low (Spawns processes) | Parallel CPU-heavy pure Python tasks |
| **Celery / RQ** | Yes | High (Network/Redis) | High (Redis/RabbitMQ) | Distributed cross-server work queues |
| **Raw PyO3 Extension** | Yes | Medium (C-API boundary) | Medium (Rebuild required) | Fixed native bindings (static packages) |

For a detailed analysis, check out the [Library Comparison Guide](https://emivvvvv.github.io/pyroxide/comparison.html).

---

## Installation

### From PyPI
```bash
pip install pyro3
```

### Build Locally
Ensure you have Rust, Python (3.8+), and `maturin` installed:
```bash
git clone https://github.com/emivvvvv/pyroxide.git
cd pyroxide
pip install maturin
maturin develop
```

---

## Quick Start

### 1. Offload Python Callables
```python
from pyroxide import task

@task
def calculate_square(x: int) -> int:
    return x * x # Runs in background OS threads

# Submit and get a handle immediately
handle = calculate_square(12)
result = handle.result() # Blocks natively (0% CPU) until complete
print(result) # 144
```

### 2. Sandboxed WebAssembly (GIL-Free)
Run computations GIL-free in a secure, virtual sandbox without compiling native code:
```python
from pyroxide import register_wasm, wasm_task

# 1. Register WebAssembly bytecode
with open("rot13.wasm", "rb") as f:
    register_wasm("rot13", f.read())

# 2. Decorate stub function
@wasm_task("rot13")
def rot13_cipher(payload: str) -> str:
    pass

# 3. Execute GIL-free on the Rust worker pool!
print(rot13_cipher("hello").result()) # "uryyb"
```

### 3. Dynamic Shared Libraries (On-the-Fly Compilation)
Compile and load native code strings on-the-fly. **Rust** (`compile_dylib`), **C** (`compile_c`), and **Zig** (`compile_zig`) are supported:
```python
from pyroxide import compile_c, dylib_task

C_SRC = """
#include <stdint.h>
#include <stdlib.h>

uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
    uint8_t* res = (uint8_t*)malloc(len);
    for (size_t i = 0; i < len; i++) {
        res[i] = (ptr[i] >= 'a' && ptr[i] <= 'z') ? (ptr[i] - 32) : ptr[i];
    }
    *out_len = len;
    return res;
}

void pyroxide_plugin_free(uint8_t* ptr, size_t len) { free(ptr); }
"""

# Compile, register and load the C library on-the-fly!
compile_c("c_upper", C_SRC)

@dylib_task("c_upper")
def to_upper_c(payload: str) -> str:
    pass

print(to_upper_c("hello from c").result())  # "HELLO FROM C"
```

---

## Dive Deeper (Documentation Book)

Detailed documentation, guides, and implementation examples are available in our [Documentation Book](https://emivvvvv.github.io/pyroxide/):

*   **Asynchronous Event Loops**: Non-blockingly await tasks using `await handle.result_async()` in FastAPI/asyncio. [Read Chapter](https://emivvvvv.github.io/pyroxide/concurrency_async.html).
*   **Batch Submissions**: Submit multiple tasks under a single lock acquisition to avoid thread contention. [Read Chapter](https://emivvvvv.github.io/pyroxide/batch_submission.html).
*   **Task Cancellation**: Gracefully abort long-running background tasks mid-flight. [Read Chapter](https://emivvvvv.github.io/pyroxide/cancellation.html).
*   **Traceback Preservation**: Capture stack traces on background worker threads and propagate them to the main thread. [Read Chapter](https://emivvvvv.github.io/pyroxide/tracebacks.html).
*   **Memory Footprint & GC**: Learn how Slab memory is reclaimed automatically using GC destructors. [Read Chapter](https://emivvvvv.github.io/pyroxide/benchmarks.html#scenario-d-long-run-memory-profile).

---

## Performance At-a-Glance

For **200 concurrent tasks** (gathered on CPython 3.11, Apple M1 Pro):

| Submission Mode | Tasks | Total Time | Avg Latency | Highlights |
|---|---|---|---|---|
| **Single Task** | 200 | 0.0051s | 25 µs (0.02ms) | Microsecond-level OS dispatch |
| **Batch Submission** | 200 | 0.0038s | 19 µs (0.01ms) | **Lock-free batch optimization** |
| **Asyncio Parallel** | 200 | 0.0120s | 60 µs (0.06ms) | Event-loop non-blocking await |

To run the performance suite locally:
```bash
python examples/benchmark.py
```

---

## Contributing

Contributions are welcome! If you'd like to improve Pyroxide or add support for additional features, feel free to open an issue or submit a pull request on GitHub.

## License

Pyroxide is licensed under any of:
* MIT License ([LICENSE-MIT](LICENSE-MIT))
* Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
* Coffeeware License ([LICENSE-COFFEE](LICENSE-COFFEE))

at your option.
