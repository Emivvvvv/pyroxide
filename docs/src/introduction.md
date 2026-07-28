# Introduction

Pyroxide is an embedded task engine for Python. It uses a bounded Rust broker to
run Python callables, WebAssembly modules, and native C-ABI plugins without an
external service.

It is suitable for background work owned by one application process. It is not a
replacement for Celery, RQ, or another distributed queue when jobs must survive
process or host failure.

## Execution modes

```text
Python application
       |
       v
bounded broker and task registry
       |
       +-- in-process worker threads: Python, WASM, native plugins
       |
       +-- bounded coordinator threads: isolated worker processes
```

| Mode | Main property | Main limitation |
| --- | --- | --- |
| `@task` | Low-overhead background Python execution | Regular CPython still uses the GIL |
| `@task(isolated=True)` | Separate interpreter and crash containment | Pickling, IPC, and process startup |
| `@wasm_task` | Memory-bounded guest with execution timeout | Requires Pyroxide's guest ABI |
| `@dylib_task` | Direct, GIL-free native execution | Trusted code only; can corrupt the host |

Pyroxide detects free-threaded CPython through `sys._is_gil_enabled()`. On a
free-threaded build, pure-Python in-process tasks may execute across CPU cores.
Measure your own extensions too: an extension can re-enable the GIL at import.

## Backpressure and lifecycle

The queue is bounded. A submission waits up to the configured queue timeout, then
raises `BufferError` if capacity remains unavailable. Batch admission is atomic:
the whole batch is accepted or none of it is.

Task results occupy registry slots until consumed or closed. Prefer `result()`
with its default `consume=True`, a `with` block, or an explicit `close()`.

Call `pyroxide.shutdown()` during application teardown. The engine cannot be
restarted in the same process. See [Operations](operations.md) for deployment,
fork, capacity, and shutdown guidance.

## Release status

`1.0.0rc1` is a release candidate. The supported API and behavior are being
stabilized for 1.0. Production evaluation should use canaries, representative
load, and failure injection before broad rollout.
