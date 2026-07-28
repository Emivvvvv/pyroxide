# Batch submission and groups

Every `@task` function has a `.batch(payloads)` helper.

```python
from pyroxide import task

@task
def square(value: int) -> int:
    return value * value

handles = square.batch([1, 2, 3, 4])
results = [handle.result() for handle in handles]
```

Batch admission reserves capacity for the entire input before creating task
records. If the queue cannot accept the whole batch before the queue timeout,
`.batch()` raises `BufferError` and accepts none of it. An empty input returns an
empty list.

Batching is an API and admission convenience. It does not promise one internal
lock acquisition or a fixed speedup; measure it for your workload.

## Task groups

`group()` manages existing handles and preserves their order.

```python
from pyroxide import group

tasks = group(square.batch([1, 2, 3, 4]))
print(tasks.status)
print(tasks.result(consume=False))
print(tasks.status)  # Completed

for handle in tasks.handles:
    handle.close()
```

- `status` reports `Failed` if any task failed, then `Cancelled`, then
  `Completed`; otherwise it reports `Running`.
- `wait()` waits sequentially for every handle.
- `result()` returns results in order.
- `cancel()` returns `True` only if every handle accepted cancellation.

The async context manager waits for all handles and groups failures. On Python
3.10, Pyroxide exposes a compatible fallback exception container with an
`exceptions` attribute; Python 3.11+ uses built-in `ExceptionGroup`.
