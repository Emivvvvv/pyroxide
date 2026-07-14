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

*   🚀 **GIL-Free Performance**: Execute CPU-intensive tasks on background threads or isolated processes without holding the Python GIL.
*   ⚡ **Microsecond Latency**: Dispatch and complete tasks in under **25 microseconds** using OS-level signaling (`Condvar`) instead of polling.
*   📦 **Zero Infrastructure**: Run entirely in-process with no Redis, RabbitMQ, or Celery worker daemons to configure or maintain.
*   💾 **Zero-Copy Transport**: Route large payloads ($\ge 1\text{MB}$) via OS Shared Memory (SHM) to bypass serialization copying bottlenecks.
*   🛡️ **Sandbox & Queue Safety**: Enforce memory/time limits on WASM tasks to prevent OOM/hangs, and use bounded queues to avoid memory runs.
*   🛠️ **Dynamic FFI Compilation**: Compile code strings on-the-fly (Rust, C, Zig) into native libraries with persistent binary caching.

---

## Pyroxide vs. Alternatives

| Feature / Metric | Pyroxide | Threading (std) | Multiprocessing | Celery / RQ |
| :--- | :---: | :---: | :---: | :---: |
| **GIL Bypass** | **✅ Yes** (WASM/dylib) | ❌ No | ✅ Yes | ✅ Yes |
| **IPC / Serialization** | **✅ None** (Shared Memory) | ✅ None | ❌ High (Pickling) | ❌ High (Network/Redis) |
| **Infrastructure** | **✅ None** (Embedded) | ✅ None | ⚠️ Low (Spawns processes) | ❌ High (Redis/RabbitMQ) |
| **Best For** | **🔥 High-perf in-process pipelines** | I/O-bound Python | CPU-heavy Python | Distributed tasks |

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

# Pure Python tasks can fully bypass the GIL with `isolated=True`
@task(isolated=True)
def heavy_computation(x: int) -> int:
    return sum(i * i for i in range(x))
```

### 2. Batch Submission & Task Groups
Submit tasks in bulk under a single lock acquisition to avoid thread contention, and manage them concurrently:
```python
from pyroxide import task, group

@task
def calculate_square(x: int) -> int:
    return x * x

payloads = [10, 20, 30, 40]

# 1. Batch submit payloads
handles = calculate_square.batch(payloads)

# 2. Bundle into a parallel TaskGroup
tg = group(handles)
print(tg.status) # "Running"

# 3. Retrieve results (consume=False preserves status metadata)
results = tg.result(consume=False)
print(results)   # [100, 400, 900, 1600]
print(tg.status) # "Completed"
```

### 3. Sandboxed WebAssembly (GIL-Free)
Run computations GIL-free in a secure, virtual sandbox without compiling native code:
```python
from pyroxide import register_wasm, wasm_task, load_wasm

# 1. Register WebAssembly bytecode
with open("rot13.wasm", "rb") as f:
    register_wasm("rot13", f.read())

# 2. Execute via decorators
@wasm_task("rot13")
def rot13_cipher(payload: str) -> str:
    pass

print(rot13_cipher("hello").result()) # "uryyb"

# 3. Or load as an Object-Oriented Proxy!
cipher = load_wasm("rot13")
print(cipher.run("hello").result()) # "uryyb"
```

### 4. Dynamic Shared Libraries (On-the-Fly Compilation)
Compile and load native code strings on-the-fly. **Rust** (`compile_dylib`), **C** (`compile_c`), and **Zig** (`compile_zig`) are supported:
```python
from pyroxide import compile_dylib, dylib_task, load_dylib

RUST_SRC = """
#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_run(ptr: *const u8, len: usize, out_len: *mut usize) -> *mut u8 {
    let input = std::slice::from_raw_parts(ptr, len);
    let s = std::str::from_utf8(input).unwrap_or("");
    let result = s.to_uppercase().into_bytes();
    *out_len = result.len();
    let boxed = result.into_boxed_slice();
    Box::into_raw(boxed) as *mut u8
}

#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_free(ptr: *mut u8, len: usize) {
    let _ = Box::from_raw(std::slice::from_raw_parts_mut(ptr, len));
}
"""

# Compile, register and load the Rust library on-the-fly!
compile_dylib("rust_upper", RUST_SRC)

# 1. Execute via decorators
@dylib_task("rust_upper")
def to_upper_rust(payload: str) -> str:
    pass

print(to_upper_rust("hello from rust").result())  # "HELLO FROM RUST"

# 2. Or load as an Object-Oriented Proxy to call any custom C-ABI symbol directly!
rust_upper = load_dylib("rust_upper")
print(rust_upper.pyroxide_plugin_run("hello from rust").result())  # "HELLO FROM RUST"
```

---

## Dive Deeper (Documentation Book)

Detailed documentation, guides, and implementation examples are available in our [Documentation Book](https://emivvvvv.github.io/pyroxide/):

*   **Asynchronous Event Loops**: Non-blockingly await tasks using `await handle.result_async()` in FastAPI/asyncio. [Read Chapter](https://emivvvvv.github.io/pyroxide/concurrency_async.html).
*   **Isolated Worker Processes**: Sandbox tasks in separate OS processes for crash safety and GIL bypass. [Read Chapter](https://emivvvvv.github.io/pyroxide/isolated_workers.html).
*   **Batch Submissions**: Submit multiple tasks under a single lock acquisition to avoid thread contention. [Read Chapter](https://emivvvvv.github.io/pyroxide/batch_submission.html).
*   **Task Cancellation**: Gracefully abort long-running background tasks mid-flight. [Read Chapter](https://emivvvvv.github.io/pyroxide/cancellation.html).
*   **Traceback Preservation**: Capture stack traces on background worker threads and propagate them to the main thread. [Read Chapter](https://emivvvvv.github.io/pyroxide/tracebacks.html).
*   **Memory Footprint & GC**: Learn how Slab memory is reclaimed automatically using GC destructors. [Read Chapter](https://emivvvvv.github.io/pyroxide/benchmarks.html#scenario-d-long-run-memory-profile).

---

## Performance At-a-Glance

We benchmarked Pyroxide against CPython's standard concurrency pools using identical compute payloads (recursive Fibonacci 20 workload) on **Apple M1 Pro (8 cores, 16GB RAM)**:

| Metric (500 Tasks) | Pyroxide `@dylib_task` | Pyroxide `@task(isolated=True)` | Pyroxide `@task` | Threading (std) | Multiprocessing |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Execution Time** | **`0.0200 s`** | **`0.0769 s`** | `0.3878 s` | `0.3742 s` | `2.0786 s` |
| **GIL Bypass** | **✅ Yes (GIL-Free)** | **✅ Yes** | ❌ No | ❌ No | ✅ Yes |
| **IPC / Serialization** | **✅ None (Shared Memory)** | **✅ Zero-Copy SHM** | ✅ None | ✅ None | ❌ High (`pickle` cost) |
| **Relative Speedup** | **🔥 100x faster** | **🔥 27x faster** | 5x faster | 5x faster | Baseline (1x) |

*   **Bypassing the Multiprocessing Bottleneck**: While Python's `ProcessPoolExecutor` takes **over 2 seconds** due to slow process spawning and heavy `pickle` IPC serialization, Pyroxide's `@dylib_task` runs native compiled plugins in just **20 milliseconds**—offering a **100x speedup** with zero-copy shared memory.

### Real-World Odoo Enterprise Arrow Ledger Audit Benchmark

To test performance under realistic enterprise data movement workloads, we ran a simulated Odoo Ledger Audit benchmark processing a **9.62 MB Apache Arrow serialized transaction recordset** (200,000 journal items) across 10 concurrent requests comparing different concurrency strategies:
*   **CPython ThreadPoolExecutor (GIL-Locked)**: `0.3221 s`
*   **Pyroxide Threaded `@task` (GIL-Locked)**: `0.3298 s` (matches Python's native scheduling overhead perfectly)
*   **ProcessPoolExecutor (Python, Pickled Pipes)**: `0.2758 s`
*   **Pyroxide SHM Isolated `@task` (Zero-Copy SHM)**: `0.3272 s`
*   **Pyroxide `@dylib_task` (C-compiled, GIL-Free)**: **`0.0091 s`** (bypasses GIL entirely)

**Key Takeaway**: By offloading the audit logic to a dynamically compiled C/Rust plugin running on Pyroxide's background thread pool, we achieve a **35.3x speedup** over CPython's standard `ThreadPoolExecutor` by completely bypassing the GIL.

To run the Odoo simulation suite locally:
```bash
python examples/odoo_poc/odoo_complex_simulation.py
```

To run the comparative and basic benchmark suites locally:
```bash
# 1. Run detailed comparative benchmarks against CPython concurrency pools
python examples/benchmarks/benchmark_vs_alternatives.py

# 2. Run basic scheduling latency and asyncio benchmarks
python examples/benchmarks/benchmark.py
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
