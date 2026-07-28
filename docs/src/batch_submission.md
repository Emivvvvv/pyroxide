# Batch submission and groups

Use `.batch(payloads)` to submit related inputs with one all-or-nothing
admission decision. Functions created by `@task` and `@dylib_task` expose the
helper directly.

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

WASM batching is available on proxy methods:

```python
from pyroxide import load_wasm

codec = load_wasm("codec")
handles = codec.run.batch([b"one", b"two"])
```

The `@wasm_task` decorator submits one payload at a time.

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

Use individual handles when each item needs different admission or cancellation
logic. See [Production operations](operations.md#backpressure) before choosing a
batch size for a bounded queue.
