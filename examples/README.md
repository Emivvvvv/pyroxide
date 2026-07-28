# Pyroxide Examples & Benchmarks

This directory contains standalone, runnable examples demonstrating Pyroxide's core features, as well as production integration and benchmark suites.

## Numbered Examples

Run any example directly with Python: `python3 examples/01_threaded_tasks.py`

| Example | Feature Demonstrated | Key API / Concept |
| :--- | :--- | :--- |
| **[01_threaded_tasks.py](01_threaded_tasks.py)** | Threaded worker pool | `PyroxidePool`, offloading Python tasks to background threads |
| **[02_batch_submissions.py](02_batch_submissions.py)** | Batch task processing | Submitting batches of functions/payloads and collecting results |
| **[03_asyncio_integration.py](03_asyncio_integration.py)** | Asyncio event loop interop | `TaskHandle.to_asyncio()` for seamless async/await integration |
| **[04_traceback_propagation.py](04_traceback_propagation.py)** | Error handling & tracebacks | Cross-thread exception capture and traceback preservation |
| **[05_isolated_processes.py](05_isolated_processes.py)** | Process isolation | Isolated worker processes for crash safety and fault tolerance |
| **[06_task_cancellation.py](06_task_cancellation.py)** | Cooperative task cancellation | Cancelling running background tasks via `TaskHandle.cancel()` |
| **[07_shared_memory_routing.py](07_shared_memory_routing.py)** | Shared memory buffer routing | Zero-copy shared memory data routing across workers |
| **[08_native_compilation.py](08_native_compilation.py)** | Native C compilation & FFI | `compile_c()`, `load_dylib()`, dynamic C plugin invocation |
| **[09_wasm_sandboxing.py](09_wasm_sandboxing.py)** | WebAssembly sandboxing | Loading WASM modules and executing sandboxed computations |
| **[10_oop_proxies_ffi.py](10_oop_proxies_ffi.py)** | OOP Proxies & stub generation | Dynamic object-oriented FFI proxies and `generate_stubs()` |

### Helpers & Shared Code
- **[example_tasks.py](example_tasks.py)**: Helper task definitions shared by examples 01 through 07.

---

## Benchmarks & Suite Integrations

- **[benchmarks/](benchmarks/)**: Reproducible Pyroxide performance benchmark suite measuring latency, throughput, and multi-core scaling.
- **[odoo_benchmark/](odoo_benchmark/)**: Production Odoo integration benchmarks, GIL-free concurrency tests, and a 25-test validation suite.
