# Exceptions and tracebacks

Pyroxide keeps the background traceback when Python work fails, so you can
diagnose the original call site after the exception crosses a task boundary.
`result()` raises a `RuntimeError` containing the exception text and formatted
traceback.

```python
from pyroxide import task

@task
def fail(_: object) -> None:
    raise ValueError("invalid payload")

try:
    fail(None).result()
except RuntimeError as error:
    print(error)
```

The original exception object is not re-raised across every execution boundary;
do not depend on catching its original Python type. Treat the reported traceback
as diagnostic text.

WASM traps, native loader errors, IPC failures, and isolated-worker crashes are
also surfaced as runtime errors with backend-specific context. A native crash in
the main process cannot be converted into a Python exception; use process
isolation for crash containment.

Use [isolated workers](isolated_workers.md) when a native crash must not take
down the main application, and inspect [Production operations](operations.md)
for failure and shutdown planning.
