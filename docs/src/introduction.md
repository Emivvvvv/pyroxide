# Introduction

Pyroxide moves work out of your application's foreground path without asking
you to deploy Redis, a worker daemon, or another service. Decorate a Python
function, submit it, and receive a handle that you can wait for or await.

That simple task API can cross four different execution boundaries:

| Mode | What it is good at |
| --- | --- |
| `@task` | Blocking I/O, background orchestration, and lightweight local work |
| `@task(isolated=True)` | CPU-bound Python and process crash containment |
| `@wasm_task` | Portable guest modules with memory and execution-time limits |
| `@dylib_task` | GIL-free calls into trusted C-ABI libraries |

Start with Python. Use process isolation when Python needs another interpreter,
WASM for portable guest code, or a compatible C ABI library written in C,
Rust, Zig, or another language for a native hot path. Pyroxide keeps all four
choices in one task system, without requiring a separate Python extension
wrapper for supported native signatures.

## Why use it?

An application often needs more than one kind of concurrency. A web handler may
offload blocking work to a thread, a calculation may need another Python
interpreter, and an extension point may need WASM isolation or an existing
native library. Using a different framework for every boundary adds deployment
and lifecycle work of its own.

Pyroxide keeps these jobs inside one bounded engine:

```text
Python application
       |
       v
bounded broker and task registry
       |
       +-- worker threads: Python, WASM, trusted native code
       |
       +-- coordinator threads: isolated worker processes
```

The queue applies backpressure instead of growing forever. Results have an
explicit lifetime. The engine reports statistics and has a defined shutdown
path. These details matter once a convenient decorator becomes production
infrastructure.

## Pick the boundary, not a slogan

`@task` is the smallest boundary. On regular CPython, pure-Python code still
uses the GIL; on free-threaded CPython it may run across cores. Isolation adds
serialization and process cost, but supplies another interpreter and contains a
worker crash.

WASM guests receive no host imports from Pyroxide and run with configured memory
and epoch-time limits. Native libraries run without the GIL but have full access
to their host process. An isolated native task gains crash containment, not an
OS security sandbox.

[Choosing an execution mode](execution_modes.md) turns these trade-offs into a
short decision guide.

## When Pyroxide is not the right tool

Pyroxide is embedded, local, and non-durable. Accepted work belongs to the
application process and is lost if that process exits.

Use a durable queue such as Celery or RQ when jobs must survive application
failure, retry durably, run on schedules, or move between hosts. Use a cluster
runtime such as Ray or Dask for distributed compute. If a standard thread or
process pool already meets the requirement, it may be the simpler choice.

Pyroxide is strongest when one application owns the work but needs more
execution choices than a single pool provides.

## Start here

1. [Install Pyroxide](installation.md).
2. [Submit your first task](getting_started.md).
3. [Choose the execution mode](execution_modes.md) that matches the workload.
4. Read [Production operations](operations.md) before a broad rollout.

`1.0.0rc1` is the compatibility preview for 1.0. Test representative payloads,
capacity limits, shutdown, and failure cases in a canary first.
