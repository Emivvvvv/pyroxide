# Traceback Preservation

When offloading Python callables to a background OS thread pool, debugging crashes can be difficult if the stack trace is lost in the thread transition.

Pyroxide captures and propagates the complete background traceback back to Python's main thread.

## Background Exceptions

If a decorated function raises an exception during execution:

```python
from pyroxide import task

@task
def failing_calculation(x: int) -> int:
    raise ValueError("Zero division or bad payload!")

handle = failing_calculation(10)

try:
    handle.result()
except RuntimeError as e:
    # Captures and logs the exact line where the background thread crashed
    print(e)
```

### Traceback Output Example

The raised `RuntimeError` contains the original Python exception type and the traceback of the background execution:

```text
ValueError: Zero division or bad payload!

Original Background Traceback:
Traceback (most recent call last):
  File "failing_calculation.py", line 4, in failing_calculation
    raise ValueError("Zero division or bad payload!")
```
