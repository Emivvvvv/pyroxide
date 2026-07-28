# Odoo benchmark

This example tests a CPU-heavy ledger-audit pipeline inside pinned Odoo
containers. It is intentionally narrower than a full Odoo performance test.

The timed boundary starts after deterministic payload extraction and ends after
all compute results return. It compares:

- serial inline Python;
- a two-worker `ProcessPoolExecutor`; and
- two Pyroxide isolated workers.

Every result is checked against the inline oracle. The run does **not** measure
ORM queries, writes, HTTP requests, Odoo multiprocess workers, or end-to-end
request latency.

## Profiles

| Profile | Source | Python |
| --- | --- | --- |
| `odoo19-py313` | pinned Odoo 19 commit | 3.13 |
| `odoo19-py314` | pinned Odoo 19 commit | 3.14 |
| `odoo-master-py314` | pinned master (“Odoo 20 preview”) | 3.14 |

There is no official Odoo 20 branch in the pinned study, so the master profile
must not be described as a released Odoo 20 build.

In the 2026-07-27 run, Odoo 19/Python 3.13 passed correctness and timing.
Odoo 19 and master on Python 3.14 were blocked before tests because their
official pinned `libsass==0.22.0` build is incompatible with CPython 3.14. The
study did not replace that dependency.

## Run

Validate without Docker:

```bash
python -m examples.odoo_benchmark.runner \
  --profile odoo19-py314 --validate
```

Run add-on correctness, then the separate timed driver:

```bash
python -m examples.odoo_benchmark.runner \
  --profile odoo19-py314 --execute
python -m examples.odoo_benchmark.runner \
  --profile odoo19-py314 --benchmark \
  --output-directory benchmark_results/odoo
```

`--benchmark` sets `PYROXIDE_MAX_TASKS_PER_WORKER=0` to measure steady state.
Use `--benchmark-recycling` to run the same workload with the default 100-task
worker lifetime and write a separate `*-recycling.json` result. Do not combine
the two modes.

The runner uses allow-listed profiles, exact source and image digests, an exact
Rust toolchain, a fresh database volume, and scoped cleanup. It refuses to
overwrite an existing result.
