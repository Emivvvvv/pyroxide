# Exceptions and tracebacks

If background Python code raises, `result()` raises a `RuntimeError` containing
the original exception text and formatted background traceback.

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
