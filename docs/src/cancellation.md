# Task cancellation

Call `cancel()` when work is no longer useful, but design for the possibility
that it has already started. Whether Pyroxide can stop it depends on its state
and execution boundary.

| Task state and mode | `cancel()` | Outcome |
| --- | --- | --- |
| Pending, any mode | `True` | Work is skipped; status becomes `Cancelled` |
| Running, isolated | `True` after termination | Worker process is terminated; status becomes `Cancelled` |
| Running, in-process Python | `False` | Callable continues and its real result is preserved |
| Running, in-process WASM or native | `False` | Guest or native call continues |
| Terminal | `False` | Status and result remain unchanged |

```python
handle = task_function(payload)
if handle.cancel():
    print("Cancellation took effect")
else:
    print("The task may already be running or finished")
```

Calling `result()` on a cancelled task raises `RuntimeError("Task cancelled")`.

Python threads, foreign native calls, and a running WASM invocation cannot be
interrupted safely at an arbitrary instruction. Use cooperative cancellation
inside your callable when you need finer control, or use `isolated=True` when
process termination is acceptable.

WASM execution timeouts are separate from user cancellation. They trap a guest
after its epoch deadline; see [WebAssembly](wasm_engine.md).

A timeout passed to `result()` or `result_async()` also stops only the wait; it
does not cancel the task. See [Getting started](getting_started.md) for handle
lifetime after a timeout.
