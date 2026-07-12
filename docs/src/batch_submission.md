# Batch Submission

When submitting a massive bulk of tasks concurrently, acquiring and releasing Pyroxide's internal read/write lock for every single task can lead to lock contention and overhead.

Pyroxide provides a bulk task submission API to optimize high-churn workloads.

## The `.batch()` Helper

Exposed on all `@task` decorated functions, `.batch()` acquires the Rust task slab write lock **exactly once** to register the entire collection of payloads:

```python
from pyroxide import task

@task
def calculate_square(x: int) -> int:
    return x * x

payloads = list(range(1000))

# Submits 1,000 tasks under a single lock acquisition
handles = calculate_square.batch(payloads)

# Retrieve results sequentially
results = [h.result() for h in handles]
```

### Performance Benefits

Acquiring the lock once for the entire batch avoids overhead and lock starvation:
*   Reduces submission overhead to **8 microseconds** per task.
*   Achieves up to a **2x latency reduction** compared to individual task loops.
*   Ideal for bulk imports, batch transactions (e.g. Odoo invoice reconciliations), and parallel parameter sweeps.
