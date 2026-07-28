# Choosing Pyroxide or another tool

Choose the execution and deployment model before comparing speed. A local
executor, an embedded multi-mode engine, a durable queue, and a cluster runtime
solve different problems.

| Need | Usually choose |
| --- | --- |
| One straightforward local thread or process pool | `concurrent.futures` |
| One embedded API for Python threads, isolated processes, WASM, and native libraries | Pyroxide |
| Durable jobs, retries, schedules, routing, or multi-host workers | Celery, RQ, Dramatiq, Temporal, or a managed queue |
| Distributed data or compute scheduling | Ray or Dask |
| One stable native algorithm known at build time | PyO3, nanobind, Cython, or another direct extension |
| A custom WASM host with its own imports and component model | Wasmtime or another dedicated host |

## What Pyroxide combines

Pyroxide is useful when one application owns the work but not every task belongs
behind the same boundary.

You can start a blocking operation on a worker thread, move CPU-bound Python to
another interpreter, run a portable guest in Wasmtime, or call a reviewed native
library without changing the caller's basic submit-and-result flow. Bounded
admission, batching, async results, cancellation rules, statistics, and shutdown
apply across those modes.

That combination is the selling point. Pyroxide is not a distributed queue
compressed into a Python extension.

## Standard executors

`ThreadPoolExecutor` is mature, built into Python, and often the simplest choice
for blocking I/O. `ProcessPoolExecutor` has a broad serialization ecosystem and
is a strong CPU-bound baseline on regular CPython.

Use them when one pool and its future API are enough. Choose Pyroxide when the
same application benefits from bounded task admission, integrated telemetry, or
several execution modes behind one interface.

All process approaches pay serialization and IPC costs. Pyroxide's isolated
mode reuses lazily created workers and routes large serialized frames through
shared memory. Python objects are still serialized; this is not end-to-end
zero-copy.

## Durable and distributed queues

Celery, RQ, Dramatiq, Temporal, and managed queue services are designed for work
that outlives one application process. Depending on the system, they provide
durability, retries, schedules, routing, monitoring, and workers on other hosts.

Pyroxide provides none of those durability guarantees. Its advantage is the
opposite trade-off: no broker, no separate worker deployment, and no network hop
for work that belongs to the current application.

No-op latency is not a fair way to rank these categories. The external systems
do more operational work because they promise different failure semantics.

## Cluster runtimes

Ray and Dask coordinate work and data across processes and machines. Choose them
when cluster scheduling, distributed object/data handling, or elastic compute is
part of the requirement.

Pyroxide stays inside one host application. It is a smaller fit for a web
service, desktop tool, automation process, or plugin host that needs local
execution choices without becoming a cluster.

## Direct native extensions

PyO3, nanobind, Cython, and C/C++ extension modules are strong choices when the
algorithm and Python API are known at build time. They offer tight typing,
wheel-time validation, and direct-call overhead.

Pyroxide native plugins favor runtime registration, background scheduling, and a
small C ABI. That flexibility brings ABI and trust risks. A scheduled native
task should not be marketed as faster than a direct binding merely because the
algorithm itself is compiled.

## Dedicated WASM hosts

Pyroxide supplies a deliberately small guest ABI, no host imports, memory
limits, and epoch deadlines. This is convenient when WASM is one execution mode
inside a Python application.

Choose a dedicated Wasmtime host when you need WASI, the Component Model, custom
imports, or a richer typed interface.

## Ask these questions

1. Must accepted work survive the application process? If yes, use a durable
   queue.
2. Must work run across hosts? If yes, use a distributed queue or cluster
   runtime.
3. Is one standard executor enough? If yes, keep the standard library.
4. Does one application need several local execution boundaries with one
   lifecycle? That is where Pyroxide fits.

See [Choosing an execution mode](execution_modes.md) for Pyroxide's internal
choices and [Benchmarking](benchmarks.md) for measured comparisons.
