# Benchmark evidence — 2026-07-27

These compact results were produced locally from the committed benchmark
harness. The repository versions canonical summaries and run metadata supporting
the published claims. Raw JSONL, logs, exploratory output, and invalid runs stay
local to avoid turning the source repository into a data archive.

## Host

- Apple M1 Pro, 8 physical / 8 logical cores
- macOS 15.7.4 (Darwin arm64)
- CPython 3.10.17, 3.11.9, 3.12.9, 3.13.8, 3.14.6, and 3.14.6t
- four workers in ranked tables
- fixed seed 1729; every returned value checked against an independent oracle
- cold start and warm steady state kept separate

Timed benchmark windows were run serially. Partial, overlapping, or otherwise
invalid runs are excluded from the versioned summaries.

## Valid local results

| Summary | Scope | Samples |
| --- | --- | ---: |
| `local/paper-3.14-2026-07-27.summary.json` | ThreadPool, Pyroxide threaded/isolated, ProcessPool | 30 blocks/cell |
| `local/python-3.14t-2026-07-27.summary.json` | same matrix with the GIL verified off | 30 blocks/cell |
| `local/python-3.14-alternatives-2026-07-27.summary.json` | adds InterpreterPool, loky, joblib | 30 blocks/cell |
| `local/python-history-2026-07-27.summary.json` | CPython 3.10–3.14 | 30 blocks/cell |
| `plugins/python-3.14-2026-07-27/` | native and WASM boundaries | 5 blocks/cell |
| `distributed/python-3.14-2026-07-27/dask-ray.summary.json` | single-node Dask and Ray | 30 blocks/cell |
| `distributed/python-3.14-2026-07-27/brokers.summary.json` | Redis-backed Celery and Dramatiq | 30 blocks/cell |
| `odoo/odoo19-py313.json` | Odoo 19 steady compute-only matched batch | 30 blocks/implementation |
| `odoo/odoo19-py313-recycling.json` | same track with 100-task worker recycling | 30 blocks/implementation |
| `reliability/rc1-5m.summary.json` | fixed-seed bounded RC1 reliability controller | 301 once-per-second samples |

The headline outcomes are in the project README and book. Summary files record
median, deterministic 95% bootstrap interval, p95, MAD, IQR, throughput, sample
count, and peak process-tree RSS.

## RC1 reliability evidence

The five-minute run used seed 1729. First/last 60-sample process-tree medians
were 123,142,144/88,375,296 RSS bytes and 9/3 descriptors; maximum child count
was 1 with a configured maximum of 2. All 211 accepted operations reached one
terminal state: 209 completed, one deliberate crash failed, and one pending
operation was cancelled. One bounded-capacity submission was rejected.

Crash recovery and post-recycle work both succeeded. The two 100-task
recycling-boundary operations took 67.784 ms and 70.157 ms, for a 70.157 ms
maximum recycling latency. Shutdown took 1.046 ms and left no queued, running,
or active tasks. The result retains RC1's 100-task worker lifetime while
documenting synchronous replacement latency. All terminal latencies were
recorded before the final sample in this run, so its final tail list was empty;
the summary includes that final list when drain work completes after sampling.

`reliability/RUN.json` records the exact command and SHA-256 hashes for the
ignored raw JSONL and tracked summary. This synthetic five-minute observation
does not establish a hard leak threshold, HTTP/Odoo service performance, or
final long-duration stability; the configurable eight-hour final soak is a
separate release activity.

## Excluded evidence

The following runs were excluded before producing the canonical summaries:

- an early isolated-task run exposed a functional-decorator pickling bug;
- an alternatives run missed the complete joblib worker warm-up;
- early direct-WASM runs used an invalid epoch deadline;
- an early cold-WASM profile repeated cold construction inside one timed cell;
- the original CFFI adapter rebuilt declarations and loaded the library on every
  call; and
- runs interrupted after measurement-window overlap was discovered are excluded.

Two earlier Odoo 19/Python 3.13 runs claimed recycling was disabled while using
Pyroxide's 100-task default. Their replacement pauses were real, but the runs
were invalid as steady-state evidence. The versioned results publish
recycling-disabled and recycling-enabled measurements separately.

Odoo 19/Python 3.14 and master/Python 3.14 produced no performance result:
both official dependency sets failed while building `libsass==0.22.0`.

## Reproduce

Create the pinned interpreter environments, then run a profile into a new path:

```bash
python -m examples.benchmarks.runner \
  --manifest examples/benchmarks/manifests/paper.toml \
  --interpreter 3.14=.benchmark-envs/py314/bin/python \
  --output benchmark_results/local/paper-new.jsonl

python -m examples.benchmarks.report \
  benchmark_results/local/paper-new.jsonl \
  --json benchmark_results/local/paper-new.summary.json \
  --markdown benchmark_results/local/paper-new.report.md
```

The runner refuses to overwrite raw data. Generate summaries from the raw output
before publishing results. Do not merge local executor, distributed/broker,
native-binding, WASM cold/warm, Odoo, or reliability tracks into one ranking:
their guarantees and timed boundaries differ.
