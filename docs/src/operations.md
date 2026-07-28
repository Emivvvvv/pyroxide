# Production operations

Pyroxide removes a separate broker and worker service, but it does not remove
operational decisions. Because the engine lives inside the application,
capacity, shutdown, and native-code risk belong in the application's design.

Start with a canary using representative payloads and failure cases.

## Configuration

Set environment variables before importing `pyroxide`. Runtime engine settings
are process-global unless documented as scoped.

| Variable | Meaning | Default |
| --- | --- | --- |
| `PYROXIDE_WORKERS` | In-process worker threads | logical CPU count |
| `PYROXIDE_QUEUE_CAPACITY` | Pending submissions across both queues | `10000` |
| `PYROXIDE_QUEUE_TIMEOUT_MS` | Admission wait; `0` rejects immediately | `1000` |
| `PYROXIDE_MAX_PROCESSES` | Concurrent isolated coordinators/processes | min(logical CPUs, 8) |
| `PYROXIDE_SHM_THRESHOLD` | Serialized frame size that selects shared memory | `1048576` |
| `PYROXIDE_MAX_IPC_FRAME_BYTES` | Maximum accepted IPC frame | `67108864` |
| `PYROXIDE_MAX_NATIVE_OUTPUT_BYTES` | Maximum copied native byte-buffer result | `67108864` |
| `PYROXIDE_MAX_TASKS_PER_WORKER` | Isolated tasks before recycle; `0` disables | `100` |
| `PYROXIDE_WORKER_STARTUP_TIMEOUT_SEC` | Isolated worker startup timeout | `5` |
| `PYROXIDE_IDLE_TIMEOUT_SEC` | Idle time before eligible worker reaping | `60` |
| `PYROXIDE_MIN_WORKERS` | Existing idle workers protected from reaping | `0` |
| `PYROXIDE_WASM_MEMORY_LIMIT_BYTES` | Per-invocation WASM memory limit | `104857600` |
| `PYROXIDE_WASM_TIMEOUT_MS` | WASM epoch deadline | `1000` |
| `PYROXIDE_WASM_TICK_MS` | Epoch increment interval | `10` |
| `PYROXIDE_CACHE_DIR` | Native compiler cache | `~/.pyroxide/cache` |
| `PYROXIDE_COMPILER_TIMEOUT_SEC` | Per native compiler command timeout | `300` |
| `PYROXIDE_DISABLE_COMPILATION` | Reject runtime source compilation | disabled |

Invalid integer engine settings fail during import instead of silently selecting
an unsafe value. `PYROXIDE_MIN_WORKERS` cannot exceed
`PYROXIDE_MAX_PROCESSES`. WASM memory cannot exceed `2**31 - 1` bytes.

Task-count recycling replaces an isolated worker synchronously, so the task
that crosses the limit pays process startup cost. Latency-sensitive services
can set `PYROXIDE_MAX_TASKS_PER_WORKER=0` after validating that their workload
does not accumulate worker state or memory. Keep recycling enabled when bounding
long-lived worker growth matters more than that occasional pause.

## Backpressure

Choose queue capacity from the maximum memory you can retain while work waits, not
only desired throughput. Submission raises `BufferError` after the queue timeout.
Batch admission requires room for the whole batch.

Application code should decide whether to retry, shed load, or return a service
error. Unbounded retries defeat backpressure.

## Metrics

```python
import pyroxide

metrics = pyroxide.stats()
```

| Key | Meaning |
| --- | --- |
| `worker_count` | Configured in-process worker threads |
| `max_processes` | Maximum concurrent isolated workers |
| `queue_capacity` | Pending admission capacity |
| `queued_tasks` | Accepted tasks not yet taken by a worker |
| `running_tasks` | Tasks currently executing |
| `active_tasks` | Task records still retained by handles |
| `submitted_tasks` | Lifetime accepted submissions |
| `rejected_tasks` | Lifetime capacity/channel rejections |
| `completed_tasks` | Lifetime successful completions |
| `failed_tasks` | Lifetime failed completions |
| `cancelled_tasks` | Lifetime effective cancellations |

Fields are read independently. During concurrent activity, `stats()` is an
approximate cross-field snapshot and may combine values from nearby moments;
use quiescent readings for drain or leak checks. If you require a linearizable
cross-field snapshot, open an issue with your use case.

Counters are process-local and reset on restart. Export them with labels supplied
by your application; Pyroxide does not run a metrics server.

## Shutdown

```python
pyroxide.shutdown(wait=True, cancel_pending=False)
```

- The default stops admission, drains accepted work, and joins workers.
- `cancel_pending=True` cancels work that has not started. Running in-process work
  still completes; running isolated work is not automatically user-cancelled.
- `wait=False` initiates shutdown and returns promptly.
- A Pyroxide worker task cannot call `shutdown(wait=True)` because shutdown would
  wait for that task. Use `wait=False` or shut down from another thread.
- Shutdown is idempotent and irreversible in the process.

Set an application-level termination grace period long enough for the largest
non-interruptible in-process task, or isolate work that must be forcibly stopped.

## Process models

Initialize Pyroxide after process managers fork or preload application code. A
broker or WebAssembly runtime inherited across `fork()` raises
`ForkSafetyError`. Spawn-based child processes can initialize their own runtime
normally.

Do not recursively submit isolated Pyroxide tasks from an isolated worker. The
worker executes its decorated Python callable directly to avoid a nested broker.

## Security checklist

- Prefer precompiled WASM and native artifacts with provenance checks.
- Set `PYROXIDE_DISABLE_COMPILATION=1` when runtime compilation is unused.
- Treat native libraries as part of the trusted computing base.
- Do not describe process isolation as a permission sandbox.
- Bound request size below the IPC and WASM limits at the application boundary.
- Run the host service with least filesystem and network privilege.
- Track Pyroxide, Wasmtime, PyO3, and Rust security advisories.

See [SECURITY.md](https://github.com/emivvvvv/pyroxide/blob/main/SECURITY.md)
for reporting and support policy.
