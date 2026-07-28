# Pyroxide

Pyroxide (`pyro3` on PyPI) is an embedded background task engine for Python,
implemented with Rust and PyO3. It provides:

- background execution for Python callables;
- process-isolated Python tasks;
- bounded WebAssembly execution with Wasmtime; and
- trusted native C-ABI plugins.

Pyroxide needs no external broker. It is not a distributed or durable job queue.

> `1.0.0rc1` is a release candidate. Its API is intended to become 1.0, but
> production adoption should start with a canary and workload-specific testing.

[Documentation](https://emivvvvv.github.io/pyroxide/) ·
[API reference](https://emivvvvv.github.io/pyroxide/api/pyroxide.html) ·
[Examples](https://github.com/emivvvvv/pyroxide/tree/main/examples)

## Requirements

- CPython 3.10 or newer
- A supported binary wheel, or Rust 1.86+ and `maturin` to build from source

```bash
pip install pyro3
```

## Choose an execution mode

| Mode | Isolation | GIL behavior | Use it for |
| --- | --- | --- | --- |
| `@task` | Same process | Python code uses the GIL on regular CPython | Blocking I/O and background orchestration |
| `@task(isolated=True)` | Worker process | Separate interpreter | CPU-bound Python or crash containment |
| `@wasm_task` | Wasmtime sandbox | Guest execution is GIL-free | Bounded, portable plugin code |
| `@dylib_task` | Same process by default | Native call is GIL-free | Trusted native code with a stable C ABI |

Free-threaded CPython can run pure-Python `@task` work in parallel. Native
libraries have unrestricted process access and can crash or corrupt the host.
`isolated=True` contains a native crash to a worker process, but it is not an OS
security sandbox.

## Quick start

```python
from pyroxide import task

@task
def square(value: int) -> int:
    return value * value

handle = square(12)
print(handle.result())  # 144
```

Use the asynchronous result API inside an event loop:

```python
result = await square(12).result_async()
```

Batch admission is all-or-nothing. If capacity is unavailable, no item in the
batch is accepted.

```python
handles = square.batch([1, 2, 3, 4])
results = [handle.result() for handle in handles]
```

For process isolation, the decorated callable and its arguments must be
pickleable and importable by a fresh Python interpreter. Define the callable at
module scope; avoid lambdas, closures, and functions defined only in `__main__`.

```python
@task(isolated=True)
def cpu_work(value: int) -> int:
    return sum(i * i for i in range(value))
```

## Cancellation and shutdown

Cancellation has explicit limits:

- pending tasks can be cancelled;
- running isolated tasks can be terminated;
- running in-process Python, WASM, and native tasks cannot be safely interrupted,
  so `cancel()` returns `False` and their result is preserved.

Shut the engine down during application teardown:

```python
import pyroxide

pyroxide.shutdown(wait=True, cancel_pending=False)
```

Shutdown is idempotent and irreversible in the current process. Do not initialize
Pyroxide before `fork()`. A child that inherits an initialized engine raises
`ForkSafetyError`; initialize it after the fork instead.

## WebAssembly

Register precompiled bytes and call an exported function:

```python
from pyroxide import register_wasm, wasm_task

register_wasm("codec", wasm_bytes)

@wasm_task("codec", "run")
def transform(payload: bytes) -> bytes:
    pass

output = transform(b"data").result()
```

WASM execution has memory and epoch-based time limits. The guest receives no host
imports from Pyroxide. Review the exact ABI and threat model in the
[WASM guide](https://emivvvvv.github.io/pyroxide/wasm_engine.html).

## Native plugins

Precompile native plugins for production when possible. Runtime compilation
invokes local compilers and executes their output; it must only receive trusted
source code.

```python
from pyroxide import register_dylib, dylib_task

register_dylib("codec", "/opt/myapp/libcodec.so")

@dylib_task("codec")
def transform(payload: bytes) -> bytes:
    pass
```

Set `PYROXIDE_DISABLE_COMPILATION=1` in production if runtime compilation is not
required. See the [native plugin guide](https://emivvvvv.github.io/pyroxide/native_plugins.html)
for the ABI and memory-safety contract.

## Operations

`pyroxide.stats()` reports approximate cross-field telemetry during active
execution: queue capacity, queued/running/active task counts, and lifetime
submitted/completed/failed/cancelled/rejected counters. See the
[operations guide](https://emivvvvv.github.io/pyroxide/operations.html).

Performance depends on payload size, execution mode, worker count, platform, and
warm-up state. See [benchmarking](https://emivvvvv.github.io/pyroxide/benchmarks.html).

## Measured performance

On an 8-core Apple M1 Pro, using four workers and 30 fresh-process blocks
(median batch makespan, lower is better):

| CPython / workload | ThreadPool | Pyroxide `@task` | ProcessPool | Pyroxide isolated |
| --- | ---: | ---: | ---: | ---: |
| 3.14, 32 CPU tasks | 65.20 ms | 55.49 ms | **17.79 ms** | 19.22 ms |
| 3.14t, 32 CPU tasks | 18.91 ms | 18.03 ms | **13.31 ms** | 15.88 ms |
| 3.14, 1,000 trivial tasks | **6.29 ms** | 18.25 ms | 157.46 ms | 52.35 ms |

Pyroxide is not the fastest choice for every workload. A process pool remains
the stronger CPU baseline on regular CPython, and `ThreadPoolExecutor` wins for
tiny in-process tasks. Pyroxide isolated mode stayed close to the process pool
in this CPU test, while its threaded mode used much less process-tree memory.
On free-threaded 3.14, the Pyroxide and thread-pool CPU confidence intervals
overlapped, so the small median difference is not a general speedup claim.

In the Odoo 19 compute-only track, Pyroxide isolated measured 30.85 ms median
and 31.64 ms p95 versus 31.77 and 32.96 ms for `ProcessPoolExecutor`. With
Pyroxide's 100-task recycling enabled, one batch paid process startup and
reached 303.52 ms; latency-sensitive deployments should evaluate that tradeoff.

The [saved study](benchmark_results/README.md) includes canonical summaries,
sample counts, confidence intervals, p95, memory, environment metadata,
Python 3.10–3.14, 3.14t, native/WASM boundaries, distributed systems, and Odoo
integration results.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
maturin develop
pytest -q
ruff check python tests examples
mypy python/pyroxide
cargo fmt --check
cargo clippy --all-targets -- -D warnings
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a change. Report
security issues using [SECURITY.md](SECURITY.md), not a public issue.

## License

Pyroxide is available under either the [MIT](LICENSE-MIT) or
[Apache-2.0](LICENSE-APACHE) license, at your option.
