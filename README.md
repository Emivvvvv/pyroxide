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
    <a href="https://github.com/emivvvvv/pyroxide/tree/main/examples">View Demo</a>
    &middot;
    <a href="https://github.com/emivvvvv/pyroxide/issues/new?labels=bug">Report Bug</a>
    &middot;
    <a href="https://github.com/emivvvvv/pyroxide/issues/new?labels=enhancement">Request Feature</a>
  </p>
</div>

---

Pyroxide is a high-concurrency, lock-free background task broker designed to bridge Python and Rust. It allows CPU-bound or blocking workloads to bypass the Python Global Interpreter Lock (GIL) with minimal memory overhead and zero CPU-sleep polling.

With Pyroxide, you can seamlessly offload tasks from Python to a background native OS thread pool. Tasks block natively on the OS kernel level using signaling primitives (`Condvar`) rather than CPU-burning sleep loops, allowing Python to yield control instantly.

## Key Features

*   **Bypass the Python GIL**: Explicitly release the Python GIL via PyO3 thread-detaching, running heavy computations concurrently on native OS threads.
*   **Zero-Overhead Status Tracking**: Avoids global lock contention using an atomic-state (`AtomicU8`) task tracking structure per task slot under a concurrent sharded/read-lock Slab architecture.
*   **Instant Condvar Signaling**: Replaces latency-inducing polling loops (`time.sleep`) with native Rust `Condvar` waking, waking waiting Python threads in microseconds with 0% CPU consumption.
*   **Dynamic Task Execution**: Seamlessly offloads dynamic Python callbacks (running inside temporary attached-GIL scopes) or executes native Rust functions completely GIL-free.
*   **Configurable Concurrency**: Set worker thread pool size dynamically at startup via environment variables.
*   **Panic Safety**: Wrapped task execution prevents Rust worker panics from crashing the host Python interpreter, gracefully marking tasks as `Failed` instead.
*   **Zero-Copy Byte Buffers**: Easily pass byte arrays, memoryviews, and columnar buffers (e.g., Apache Arrow) across the C-ABI without copy overhead.
*   **Full Type-Hinting**: Exposes advanced typing generic `@overload` signatures, offering full autocomplete support for modern IDEs.

---

## Installation

### From PyPI (Recommended)

```bash
pip install pyro3
```

### Build and Install locally

Ensure you have Rust, Python (3.8+), and `maturin` installed.

### Build and Install locally

Compile and install the C-extension into your active virtual environment:

```bash
# Clone the repository
git clone https://github.com/emivvvvv/pyroxide.git
cd pyroxide

# Compile and install editable build using maturin
pip install maturin
maturin develop
```

---

## Performance & Validation

We benchmarked Pyroxide against the baseline polling-based task brokers (using a 1ms simulated execution payload to isolate broker overhead):

### 1. Latency (Single-Threaded Sequential Wait)
| Tasks | Baseline (10ms Polling Loop) | Pyroxide (Condvar + Lock-Free) | **Latency Reduction** |
| :--- | :--- | :--- | :--- |
| **10 Tasks** | `1.0180s` | `0.0002s` (0.2ms) | **~5,000x faster** |
| **50 Tasks** | `3.5289s` | `0.0008s` (0.8ms) | **~4,400x faster** |
| **Avg. Latency** | **`70.58ms`** | **`0.02ms` (20µs)** | **3,500x less overhead** |

### 2. Multi-Threaded Throughput (40 Concurrent Submissions)
| Threads | Baseline (Lock Contention) | Pyroxide (Lock-Free) | **Speedup** |
| :--- | :--- | :--- | :--- |
| **2 Threads** | `10.1848s` | `0.0032s` (3.2ms) | **3,180x faster** |
| **4 Threads** | `5.1193s` | `0.0013s` (1.3ms) | **3,930x faster** |
| **8 Threads** | `2.5624s` | `0.0015s` (1.5ms) | **1,700x faster** |

---

## Configuration

Pyroxide can be configured via environment variables before the engine initializes:

*   `PYROXIDE_WORKERS`: Sets the number of background worker threads in the pool. Defaults to the number of CPU cores (`available_parallelism`).

```bash
export PYROXIDE_WORKERS=4
python my_app.py
```

---

## Quick Start

### 1. Offloading Python Callables (Default)
By default, `@task` runs the decorated Python function in the background pool.

```python
from pyroxide import task

@task
def calculate_square(x: int) -> int:
    # Runs on background OS threads in the Rust pool
    return x * x

# Submit and get a handle immediately
handle = calculate_square(12)
print(f"Task status: {handle.status}")

# Blocks natively (0% CPU) until complete, then returns result
# Automatically evicts the task from the Rust Slab once retrieved (consume=True)
result = handle.result()
print(f"Result: {result}") # Output: 144
```

### 2. Offloading Native Rust Tasks (GIL-Free)
To completely bypass the Python GIL and run logic natively in Rust:

```python
from pyroxide import task

# Passes the string to Rust which processes it completely GIL-free
@task(native=True)
def native_uppercase(payload: str) -> None:
    pass

handle = native_uppercase("hello pyroxide")
result = handle.result()
print(f"Result: {result}") # Output: b"HELLO PYROXIDE"
```

### 3. Graceful Memory Reclamation & Retaining Results
By default, `result()` consumes and evicts the task. If you want to keep the task in the Slab (e.g. to check status or result again later), set `consume=False`. It will be automatically cleaned up later via the Python garbage collector when the handle reference is deleted:

```python
import gc
from pyroxide import task
from pyroxide._pyroxide import get_slab_size

handle = calculate_square(10)
print(get_slab_size()) # Output: 1

# Retrieve result but retain task in the Slab
result = handle.result(consume=False)

# Deleting the Python TaskHandle reference forces GC eviction in the Rust Slab
del handle
gc.collect()

print(get_slab_size()) # Output: 0 (No memory leaked)
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
