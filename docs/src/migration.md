# Migrating from 0.10 to 1.0

`1.0.0rc1` is the compatibility preview for 1.0. Test it before upgrading a
production service.

## Runtime requirements

- Minimum Python is now 3.10.
- Source builds require Rust 1.86 or newer.
- Wheels use the CPython 3.10 stable ABI where supported; free-threaded CPython
  uses dedicated wheels.

## Behavior changes

### Cancellation

`cancel()` no longer reports success for running in-process work it cannot stop.
Pending work remains cancellable. Running isolated work is cancelled by
terminating its worker. Audit code that assumed `True` meant a Python thread,
native call, or WASM call had been interrupted.

### Bounded admission

Queue capacity applies atomically before task records are created. Batch
submission is all-or-nothing. Handle `BufferError` and choose an explicit
`PYROXIDE_QUEUE_TIMEOUT_MS` for overload behavior.

### Lifecycle and fork

Call `pyroxide.shutdown()` during teardown. The engine rejects new work after
shutdown and cannot restart in that process. Initialize after `fork()`; inherited
engines raise `ForkSafetyError`.

### Isolated workers

Isolated concurrency is bounded by `PYROXIDE_MAX_PROCESSES`, defaulting to at most
eight. Workers are created lazily. `PYROXIDE_MIN_WORKERS` retains already-created
idle workers; it does not prewarm them.

### Validation and limits

Invalid engine environment values now fail at import. IPC frames and WASM guest
input/output ranges are checked before allocation or memory access. If an existing
deployment relied on larger frames, set an intentional limit and validate memory
capacity first.

## Packaging

The license expression is now `MIT OR Apache-2.0`. The Coffeeware option was
removed. The package ships a `py.typed` marker.

## Recommended rollout

1. Run the test suite on `1.0.0rc1` under your oldest and newest Python versions.
2. Load-test bounded admission and record rejection behavior.
3. Exercise cancellation, worker crashes, fork behavior, and graceful shutdown.
4. Canary one service instance and monitor queue, failure, memory, and latency
   metrics.
5. Report RC compatibility problems before adopting final 1.0.
