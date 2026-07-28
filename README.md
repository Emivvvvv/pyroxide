<!-- PROJECT LOGO -->
<br />
<div align="center">
  <a href="https://github.com/emivvvvv/pyroxide">
    <img src="https://raw.githubusercontent.com/emivvvvv/pyroxide/main/pyroxide.svg" alt="Pyroxide" width="88" height="88">
  </a>

  <h1 align="center">Pyroxide</h1>

  <p align="center">
    Python tasks, isolated processes, WebAssembly, and C ABI shared libraries.<br />
    One embedded engine. One task API.
  </p>

  <p align="center">
    <a href="https://pypi.org/project/pyro3/"><img src="https://img.shields.io/badge/release-1.0.0rc1-orange.svg" alt="Release 1.0.0rc1"></a>
    <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+"></a>
    <a href="https://www.rust-lang.org/"><img src="https://img.shields.io/badge/rust-1.86%2B-black.svg" alt="Rust 1.86+"></a>
    <a href="https://github.com/emivvvvv/pyroxide/actions/workflows/ci.yml"><img src="https://github.com/emivvvvv/pyroxide/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="https://github.com/emivvvvv/pyroxide/blob/main/LICENSE-MIT"><img src="https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-green.svg" alt="MIT or Apache-2.0"></a>
  </p>

  <p align="center">
    <a href="https://emivvvvv.github.io/pyroxide/"><strong>Read the user manual »</strong></a>
    <br />
    <a href="https://emivvvvv.github.io/pyroxide/api/pyroxide.html">API reference</a>
    ·
    <a href="https://github.com/emivvvvv/pyroxide/tree/main/examples">Examples</a>
    ·
    <a href="https://github.com/emivvvvv/pyroxide/issues/new?labels=bug">Report a bug</a>
    ·
    <a href="https://github.com/emivvvvv/pyroxide/issues/new?labels=enhancement">Request a feature</a>
  </p>
</div>

---

Run work in the background without Redis, worker daemons, or another service to
operate. Start with a Python decorator; choose threads, isolation, WASM, or
native execution for each workload.

## Why Pyroxide?

- **Nothing else to deploy.** The task engine lives in your application. There
  is no broker, separate worker service, or network hop for local work.
- **Choose the boundary per task.** Keep ordinary work lightweight, move
  CPU-bound Python into another interpreter, run portable plugins in Wasmtime,
  or call a reviewed C ABI shared library without holding the GIL.
- **One lifecycle to operate.** Bounded admission, batches, async results,
  cancellation rules, statistics, fork safety, and explicit shutdown are part
  of the same engine.
- **Move toward native speed without redesigning the caller.** A task can start
  as Python and later move behind a WASM or native boundary while keeping the
  submit-and-result workflow.

Free-threaded CPython can also run pure-Python `@task` work in parallel. On
regular CPython, use isolation for parallel CPU-bound Python.

## Four execution modes

| Mode | Reach for it when you need |
| --- | --- |
| [`@task`](https://emivvvvv.github.io/pyroxide/getting_started.html) | Lightweight background work inside the application |
| [`@task(isolated=True)`](https://emivvvvv.github.io/pyroxide/isolated_workers.html) | CPU-bound Python or process crash containment |
| [`@wasm_task`](https://emivvvvv.github.io/pyroxide/wasm_engine.html) | Portable guest code with memory and execution-time limits |
| [`@dylib_task`](https://emivvvvv.github.io/pyroxide/native_plugins.html) | GIL-free calls into trusted native libraries |

## Where it fits

| Choose | When |
| --- | --- |
| **Pyroxide** | Work belongs to one application and benefits from different execution boundaries |
| `ThreadPoolExecutor` or `ProcessPoolExecutor` | A basic local thread or process pool is enough |
| Celery, RQ, or another durable queue | Jobs must survive application failure, run on schedules, retry durably, or move across hosts |
| Ray or Dask | The workload needs a distributed compute runtime |

Pyroxide does not try to turn local work into a distributed system. Its strength
is putting several useful local execution models behind one small API. The
[comparison guide](https://emivvvvv.github.io/pyroxide/comparison.html) covers
the trade-offs, and the [benchmark study](https://github.com/emivvvvv/pyroxide/blob/main/benchmark_results/README.md)
publishes reproducible results, including workloads where the standard library
wins.

## Quick start

Install the `pyro3` package and import it as `pyroxide`:

```bash
pip install pyro3
```

### Python task

```python
from pyroxide import task

@task
def square(value: int) -> int:
    return value * value

handle = square(12)
print(handle.result())  # 144
```

Inside an event loop, use `await handle.result_async()` instead of blocking the
loop. See [Concurrency and asyncio](https://emivvvvv.github.io/pyroxide/concurrency_async.html).

### Isolated Python

Add `isolated=True` when CPU-bound Python needs another interpreter or when a
trusted native crash must not take down the main application. Workers are
reused, bounded, and recycled rather than spawned for every task.

`tasks.py`:

```python
from pyroxide import task

@task(isolated=True)
def calculate(value: int) -> int:
    return sum(i * i for i in range(value))
```

`app.py`:

```python
from tasks import calculate

print(calculate(1_000_000).result())
```

### WebAssembly

Register a precompiled `.wasm` module when plugin code needs a portable
application boundary. Pyroxide supplies no host imports and applies memory and
epoch-time limits to each call.

```python
from pathlib import Path
from pyroxide import load_wasm, register_wasm, wasm_task

register_wasm("codec", Path("codec.wasm").read_bytes())

@wasm_task("codec", "compress")
def compress(payload: bytes) -> bytes:
    pass

@wasm_task("codec", "decompress")
def decompress(payload: bytes) -> bytes:
    pass

compressed = compress(b"data").result()

codec = load_wasm("codec")
restored = codec.decompress(compressed).result()
handles = codec.compress.batch([b"first", b"second"])
```

A module can export many functions. Bind each export with its own `@wasm_task`
decorator, or use one `load_wasm()` proxy and call exports as methods such as
`codec.compress()` and `codec.decompress()`. Proxy methods also support
`.batch(...)`.

### Native shared library

Compatible shared libraries may be written in C, Rust, Zig, or another language
using Pyroxide's supported byte-buffer C ABI. Register a reviewed precompiled
library:

```python
from pyroxide import dylib_task, load_dylib, register_dylib

register_dylib("codec", "./libcodec.so")

@dylib_task("codec", "compress")
def compress(payload: bytes) -> bytes:
    pass

@dylib_task("codec", "decompress")
def decompress(payload: bytes) -> bytes:
    pass

compressed = compress(b"data").result()

codec = load_dylib("codec")
restored = codec.decompress(compressed).result()
handles = codec.compress.batch([b"first", b"second"])
```

A library can export many functions. Bind each export with its own
`@dylib_task` decorator, or use one `load_dylib()` proxy and call exports as
methods. Decorators and proxy methods both support `.batch(...)`.

No custom Python extension wrapper is required. During development,
`compile_c()`, `compile_rust()`, and `compile_zig()` can build and register
trusted source. Production can load a reviewed `.so`, `.dylib`, or `.dll`.

The manual documents serialization, ABI ownership, guest limits, and failure
semantics before you cross any of these boundaries.

## Performance Benchmarks

These Apple M1 Pro reference runs report median complete batch time. The Python
executor table used four workers. Lower is better.

### Python tasks and isolation

| CPython and workload | `ThreadPoolExecutor` | Pyroxide `@task` | `ProcessPoolExecutor` | Pyroxide isolated |
| --- | ---: | ---: | ---: | ---: |
| 3.14, 32 CPU tasks | 65.20 ms | 55.49 ms | **17.79 ms** | 19.22 ms |
| 3.14t, 32 CPU tasks | 18.91 ms | 18.03 ms | **13.31 ms** | 15.88 ms |
| 3.14, 1,000 trivial tasks | **6.29 ms** | 18.25 ms | 157.46 ms | 52.35 ms |

### Native, WebAssembly, and application workloads

| Workload | Pyroxide | Comparison |
| --- | ---: | ---: |
| Scheduled native call, 1 KiB Rust workload | 22.56 µs | Direct PyO3, nanobind, CFFI, and ctypes: 7.26-8.59 µs |
| Warm WebAssembly call, same 1 KiB workload | **47.57 µs** | Direct `wasmtime-py` host: 80.24 µs |
| Odoo 19 compute-only, Python 3.13, 8 payloads, 2 workers | **30.85 ms** | `ProcessPoolExecutor`: 31.77 ms; inline: 60.37 ms |

What to expect:

- **`@task`:** Competitive with `ThreadPoolExecutor` for substantial work.
  The standard thread pool wins for extremely small tasks.
- **Isolated Python:** Close to `ProcessPoolExecutor` on CPU work, with lower
  overhead in the small-task batch and the same Pyroxide task workflow.
- **Free-threaded Python:** `@task` can run Python across cores while retaining
  Pyroxide handles, batching, statistics, and lifecycle controls.
- **Native and WASM:** Native scheduling adds overhead compared with a direct
  binding. In return, it joins compiled code to the task system. WASM provides
  a portable, resource-limited guest boundary through that same system.

The five-minute RC1 run accounted for all 3,080 accepted operations and
recovered after 300 deliberate worker crashes. The full
[benchmark study](https://github.com/emivvvvv/pyroxide/blob/main/benchmark_results/README.md)
contains setup details, confidence intervals, memory results, distributed
systems, and workloads where other tools win.

## Know the boundaries

> **1.0.0rc1 is a release candidate.** Its API is intended to become 1.0. Start
> production adoption with a canary and representative failure testing.

- Pyroxide is embedded, not durable or distributed. Queued work is lost if the
  application process exits.
- Pending tasks can be cancelled. Running isolated work can be terminated;
  running in-process Python, WASM, or native work cannot be safely interrupted.
- Native libraries have unrestricted access to the host process. Isolation can
  contain a native crash to a worker process, but it is not an OS security
  sandbox.
- Pyroxide gives WASM guests no host imports and applies memory and epoch-time
  limits. Treat that as an application isolation boundary, not an absolute
  security promise.

## Explore

- [User manual](https://emivvvvv.github.io/pyroxide/) - guided workflows and
  technical contracts
- [Production operations](https://emivvvvv.github.io/pyroxide/operations.html)
  - capacity, telemetry, shutdown, and fork safety
- [Benchmark evidence](https://github.com/emivvvvv/pyroxide/blob/main/benchmark_results/README.md)
  - reproducible measurements and saved study metadata
- [Examples](https://github.com/emivvvvv/pyroxide/tree/main/examples) - runnable
  task, isolation, WASM, and native integrations
- [API reference](https://emivvvvv.github.io/pyroxide/api/pyroxide.html) - public
  Python interface
- [Security policy](https://github.com/emivvvvv/pyroxide/blob/main/SECURITY.md)
  - supported releases and private reporting
- [Contributing](https://github.com/emivvvvv/pyroxide/blob/main/CONTRIBUTING.md)
  - development workflow and change requirements

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
maturin develop
pytest -q
```

Read [CONTRIBUTING.md](https://github.com/emivvvvv/pyroxide/blob/main/CONTRIBUTING.md)
before submitting a change. Report security issues through
[SECURITY.md](https://github.com/emivvvvv/pyroxide/blob/main/SECURITY.md), not a
public issue.

## License

Choose either the [MIT](https://github.com/emivvvvv/pyroxide/blob/main/LICENSE-MIT)
or [Apache-2.0](https://github.com/emivvvvv/pyroxide/blob/main/LICENSE-APACHE)
license.
