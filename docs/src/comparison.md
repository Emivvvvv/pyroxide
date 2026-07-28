# Choosing the right tool

Pyroxide is one option among several different execution models. Choose by
failure semantics and deployment needs before comparing speed.

| Need | Usually choose | Why |
| --- | --- | --- |
| Simple background I/O in one process | `ThreadPoolExecutor` or `@task` | No IPC or external service |
| CPU-bound Python on regular CPython | Process pool or isolated task | Separate interpreters bypass the GIL |
| CPU-bound Python on CPython 3.14t | Thread pool or `@task`, after testing extensions | Free-threaded execution may scale in-process |
| Durable jobs, retries, schedules, multi-host workers | Celery, RQ, Dramatiq, Temporal, or a managed queue | Survives application process failure |
| Stable native algorithm shipped with an application | PyO3, nanobind, Cython, or a reviewed Pyroxide plugin | Compiled implementation and explicit ABI |
| Bounded portable plugin execution | Pyroxide WASM or another Wasmtime host | Guest memory and execution controls |

## Standard executors

`ThreadPoolExecutor` is mature, standard-library infrastructure and is often the
simplest choice for I/O. Pyroxide adds task handles, a bounded Rust broker,
batch admission, WASM/native backends, and integrated metrics.

`ProcessPoolExecutor` and loky have broader Python serialization ecosystems.
Pyroxide's isolated mode uses a reusable, lazily created pool and shared-memory
routing for large serialized frames. All process approaches still pay
serialization and IPC costs.

In the July 2026 four-worker CPU run, loky, `ProcessPoolExecutor`, and
`InterpreterPoolExecutor` all beat in-process Pyroxide on regular CPython 3.14.
Pyroxide isolated stayed close to `ProcessPoolExecutor` in the smaller paper
cell. Choose it for its unified task API, bounded admission, cancellation, and
crash containment—not because it always wins a throughput chart.

## Free-threaded CPython

Free-threaded CPython removes the GIL as the universal reason to use processes,
but it does not compile Python bytecode or guarantee that every extension remains
free-threaded. Compare the same pure-Python workload on the exact interpreter and
extension set you will deploy.

On the tested 3.14t build, Pyroxide threaded and `ThreadPoolExecutor` had
overlapping CPU-batch bootstrap intervals. Free-threading made both competitive
with process approaches, but did not make Pyroxide a universal winner.

## Native extension pools

PyO3 with Rayon, C++ with OpenMP/TBB, and similar extensions are strong choices
when the API and algorithm are known at build time. They provide tighter typing
and wheel-time validation. Pyroxide native plugins favor runtime registration and
a small C ABI, with the corresponding ABI and trust risks.

## Distributed queues

Celery and similar systems incur serialization, broker, and network work because
they provide durability, routing, retries, scheduling, and multi-host workers.
Pyroxide provides none of those guarantees. Comparing no-op latency alone is not
a fair product comparison.

Ray and Dask similarly add scheduling and distribution capabilities. Their
results are reported in a separate operational track and are not inserted into
the local-executor ranking.

See [Benchmarking](benchmarks.md) for a reproducible evaluation method.
