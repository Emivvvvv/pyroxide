# Isolated worker processes

Use `isolated=True` when CPU-bound Python needs another interpreter on regular
CPython, or when trusted native code needs process crash containment.

```python
from pyroxide import task

@task(isolated=True)
def calculate(value: int) -> int:
    return sum(i * i for i in range(value))

print(calculate(1_000_000).result())
```

Isolation adds serialization, IPC, and possible cold-start cost. It is a
deliberate boundary, not a default performance upgrade.

## Importability contract

The callable and payload are serialized for a fresh interpreter. The callable
must be defined at module scope in an importable module. Arguments and results
must be pickleable.

Avoid:

- lambdas and closures;
- nested functions;
- definitions available only while a script is `__main__`; and
- process-local resources such as open sockets, locks, and database connections.

Create process-local resources inside the worker callable instead.

## Pool behavior

- Workers are created lazily when isolated work arrives.
- At most `PYROXIDE_MAX_PROCESSES` coordinators and worker processes execute
  isolated work concurrently.
- An idle worker may be reaped after `PYROXIDE_IDLE_TIMEOUT_SEC`.
- `PYROXIDE_MIN_WORKERS` protects that many already-created idle workers from
  reaping; it does not pre-create them.
- A worker is recycled after `PYROXIDE_MAX_TASKS_PER_WORKER`; `0` disables
  task-count recycling.

Small frames travel over a private local socket or named pipe. Large serialized
frames use shared memory when they meet `PYROXIDE_SHM_THRESHOLD`. This avoids
copying a large frame through the socket, but it is not end-to-end zero-copy:
Python objects are still serialized and copied into and out of shared memory.

The Unix socket directory is private to the user and created with mode `0700`.
IPC frame and metadata lengths are checked before allocation.

## Cancellation and crashes

Cancelling a running isolated task terminates its worker and reports cancellation
only after the child is no longer alive. A crashed worker surfaces an error for
the task; later work can use another worker.

Isolation is crash containment, not a security sandbox. A worker normally has the
same user identity, filesystem visibility, and network access as the parent.

Pyroxide mitigates orphan workers when the parent disappears:

- macOS uses a process-exit event through `kqueue`;
- Linux and other Unix platforms poll the parent relationship;
- Windows polls the parent process handle.

Detection is best effort and may take roughly one polling interval on platforms
without an event notification.

## Fork safety

Do not initialize Pyroxide before calling `fork()`. An inherited engine contains
threads and synchronization state that cannot be used safely in the child.
Pyroxide detects this and raises `ForkSafetyError`. Initialize Pyroxide separately
after the fork, or use a spawn-based process model.

See [Choosing an execution mode](execution_modes.md) for alternatives and
[Production operations](operations.md) for capacity, recycling, and shutdown
settings.
