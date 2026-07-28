# Benchmarking

Pyroxide does not publish a universal latency or speedup claim. Results change
with hardware, OS, Python build, worker count, payload size, execution mode,
compiler, and whether workers are warm.

The scripts under `examples/benchmarks/` are evaluation tools, not product
guarantees.

## Fair-comparison rules

1. Compare systems with the same durability and isolation semantics.
   `@task` is comparable to an in-process executor; Celery is a distributed,
   durable queue and answers a different problem.
2. Use identical work and input data. Do not compare compiled native work with
   interpreted Python and attribute the difference to scheduling.
3. Fix and report worker counts, affinity, power mode, and dependency versions.
4. Separate cold results from warm steady-state results. Process creation, JIT,
   module loading, and runtime compilation belong in cold-start measurements.
5. Run enough repetitions and report distributions such as median and p95, not
   one best sample.
6. Verify every result and report failures. A fast benchmark that skipped work is
   invalid.
7. Save the command, configuration, platform metadata, and raw machine-readable
   output with any published number.

## Recommended comparisons

| Question | Appropriate comparison |
| --- | --- |
| In-process Python scheduling | `@task` vs `ThreadPoolExecutor` |
| CPU-bound Python isolation | isolated tasks vs `ProcessPoolExecutor` or loky |
| Free-threaded Python | same Python function and worker count on CPython 3.14t |
| Native execution | same compiled algorithm through Pyroxide and a direct binding |
| WASM overhead | same module and ABI through comparable Wasmtime hosts |
| Large IPC payload | same serialization format, payload, warm pool, and process count |

Celery, RQ, and similar systems may be included to explain architectural cost,
but their broker durability, retry, routing, and multi-host behavior must be
enabled and disclosed. They are not direct substitutes for an embedded engine.

## July 2026 reference run

The saved reference run used macOS 15.7.4 on an Apple M1 Pro with 8 physical
cores. Each ranked cell used four workers and 30 fresh-process blocks. Values
below are median complete-batch makespans; lower is better.

| CPython / batch | ThreadPool | Pyroxide threaded | ProcessPool | Pyroxide isolated |
| --- | ---: | ---: | ---: | ---: |
| 3.14, 32 CPU tasks | 65.20 ms | 55.49 ms | **17.79 ms** | 19.22 ms |
| 3.14t, 32 CPU tasks | 18.91 ms | 18.03 ms | **13.31 ms** | 15.88 ms |
| 3.14, 1,000 trivial tasks | **6.29 ms** | 18.25 ms | 157.46 ms | 52.35 ms |

The result is mixed, which is the useful conclusion:

- regular CPython still needs processes or independent interpreters for
  CPU-parallel Python;
- Pyroxide isolated was 8% slower than `ProcessPoolExecutor` in the 3.14 CPU
  cell, not faster;
- Pyroxide threaded used 33 MiB peak process-tree RSS in that cell, versus
  147 MiB isolated and 154 MiB for the process pool;
- on 3.14t, Pyroxide threaded recorded a 4.7% lower median than the thread pool,
  but their bootstrap intervals overlapped; and
- the standard thread pool was about three times faster than Pyroxide threaded
  for the trivial-task batch.

The broader CPython 3.14 comparison used 100 CPU tasks:

| Backend | Median | Peak process-tree RSS |
| --- | ---: | ---: |
| loky | **59.07 ms** | 179 MiB |
| `ProcessPoolExecutor` | 60.47 ms | 156 MiB |
| `InterpreterPoolExecutor` | 65.97 ms | 74 MiB |
| joblib | 96.20 ms | 211 MiB |
| Pyroxide threaded | 171.14 ms | 34 MiB |
| `ThreadPoolExecutor` | 182.83 ms | 30 MiB |

Across CPython 3.10–3.14, Pyroxide threaded was 12–14% faster than the thread
pool for that same CPU batch, but both remained much slower than the process
pool. This is scheduler efficiency under the GIL, not CPU parallelism.

The native/WASM boundary track used the same 1 KiB Rust workload. Direct PyO3,
nanobind, warmed CFFI, and ctypes calls measured 7.26, 7.38, 7.63, and 8.59 µs
respectively. A scheduled Pyroxide dylib call measured 22.56 µs; it includes
task submission and result handling, so it is not a direct-binding speed claim.
Warm Pyroxide WASM measured 47.57 µs versus 80.24 µs for the tested direct
`wasmtime-py` host. Cold Wasmtime compile, instantiate, and call measured
41.12 ms and is reported separately.

Distributed and durable systems were measured in separate tracks. In the
single-node four-worker run, Ray processed 7,542 trivial tasks/s and 1,690 CPU
tasks/s; Dask processed 685 and 658 tasks/s. Ray used about 963–978 MiB peak
process-tree RSS versus 329–335 MiB for Dask. With Redis, late acknowledgement,
JSON serialization, two workers, and result retrieval enabled, Celery processed
564 payload tasks/s and 251 CPU tasks/s; Dramatiq processed 248 and 93 tasks/s.
These numbers compare each track's operational cost and must not be ranked
against the embedded executors.

The Odoo track produced one valid environment: pinned Odoo 19 on Python 3.13.
For eight ledger payloads, two workers, and 30 matched blocks, steady-state
median compute-only batch time was 60.37 ms inline, 31.77 ms with
`ProcessPoolExecutor`, and 30.85 ms with Pyroxide isolated. Their p95 values
were 61.59, 32.96, and 31.64 ms; Pyroxide's maximum was 33.41 ms.

A separate run retained Pyroxide's default 100-task worker lifetime. Its median
was 31.02 ms, but synchronous worker replacement produced one 303.52 ms batch.
Two earlier runs were invalidated because they claimed recycling was disabled
while using that default. The controlled runs show stable steady-state
performance and a predictable recycling latency cost; they do not show random
Pyroxide stalls. This test excludes ORM extraction, writes, HTTP, and Odoo
worker-process overhead.

Odoo 19 and pinned Odoo master (“Odoo 20 preview”) could not install their
official `libsass==0.22.0` requirement on Python 3.14. No 3.14 Odoo timing was
published and no unpinned dependency was substituted.

Canonical summaries, sample counts, environment metadata, native/WASM
boundaries, distributed tracks, and Odoo results are versioned in
`benchmark_results/`. Raw observations, logs, and invalid runs are generated
locally by the reproducible harness but are not committed. The measurements are
evidence for this machine and workload, not capacity-planning constants.

## Running the local scripts

Build Pyroxide in the active environment first:

```bash
maturin develop
python examples/benchmarks/benchmark.py
python examples/benchmarks/benchmark_large_payload.py
PYROXIDE_WORKERS=8 PYROXIDE_MAX_PROCESSES=8 \
  python examples/benchmarks/benchmark_vs_alternatives.py --workers 8
```

The comparison script exits instead of publishing mismatched results unless
both Pyroxide pool sizes equal `--workers`.

Optional comparisons require their own dependencies and interpreters. Record
those exact versions in results. Do not copy numbers from this book into capacity
plans; benchmark the deployed platform with representative task sizes and queue
pressure.

The generic runner refuses the reliability manifest. A one-task throughput cell
is not a 30-minute or four-hour soak, so reliability evidence must come from a
dedicated duration-aware harness.

## Production evaluation

Performance is only one release criterion. A useful soak test also records:

- accepted and rejected submissions under bounded capacity;
- queued and running tasks;
- completion, failure, cancellation, and timeout behavior;
- RSS and file-descriptor growth;
- isolated worker churn and orphan cleanup;
- shutdown drain time; and
- p50, p95, and p99 end-to-end latency.

Use `pyroxide.stats()` for engine counters and your application telemetry for
request-level latency and correctness.
