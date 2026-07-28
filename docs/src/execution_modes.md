# Choosing an execution mode

`@task` is the default for local background work. Move a workload to a stronger
boundary only when you need another interpreter, crash containment, portable
guest isolation, or a native ABI.

| Mode | Boundary | Best use | Main cost |
| --- | --- | --- | --- |
| `@task` | Worker thread in the application | Blocking I/O and background orchestration | Pure Python uses the GIL on regular CPython |
| `@task(isolated=True)` | Reused worker process | Another interpreter for CPU-bound Python and crash containment | Serialization, IPC, and process startup |
| `@wasm_task` | Wasmtime guest in a worker thread | Portable, resource-limited guest modules | Guest ABI and data-copy cost |
| `@dylib_task` or `load_dylib()` | Native call in a worker thread by default | Compatible native C-ABI libraries without a Python extension wrapper | Native memory-safety and host-process risk |

All four modes return a `TaskHandle` and support the same basic
submit-and-result workflow. Their failure and cancellation semantics differ.

## Blocking or background Python

Use `@task` when work can safely run in the application process:

```python
from pyroxide import task

@task
def fetch_report(report_id: int) -> bytes:
    return read_report(report_id)
```

This is the lowest-overhead option. On regular CPython, the GIL still governs
pure-Python execution. Free-threaded CPython may run Python tasks across cores,
although an imported extension can re-enable the GIL.

## CPU-bound Python or crash containment

Use `@task(isolated=True)` when regular CPython needs another interpreter for
CPU work. It also supplies process crash containment:

```python
@task(isolated=True)
def calculate(limit: int) -> int:
    return sum(i * i for i in range(limit))
```

Isolation is also useful when trusted native code might abort or segfault. The
callable and its data must be serializable and importable by a fresh Python
interpreter. Read [Isolated worker processes](isolated_workers.md).

## Portable guest code

Use `@wasm_task` for a portable guest module that should receive no file, socket,
or environment imports from Pyroxide and should run with configured memory and
epoch-time limits.

The module must implement Pyroxide's guest ABI. Treat WASM as an
application-level isolation boundary, not an absolute security promise. Read
[WebAssembly execution](wasm_engine.md).

## Trusted native libraries

Use `@dylib_task` or `load_dylib()` to call a compatible `.so`, `.dylib`, or
`.dll` through a stable C ABI without holding the Python GIL or writing a
separate Python extension wrapper.

Native code has unrestricted access to its process. `isolated=True` can contain
a crash to a worker process, but it does not make the library safe or sandboxed.
Read [Native shared-library plugins](native_plugins.md).

## A quick decision

- Need ordinary local background work? Start with `@task`.
- Need multiple cores for Python on regular CPython? Use isolation.
- Need a portable, resource-bounded guest? Use WASM.
- Already have reviewed native code or need a C ABI? Use a native plugin.
- Need durable jobs or multiple hosts? Choose a different system; see
  [Choosing the right tool](comparison.md).
