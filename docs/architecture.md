# Architecture

This document describes the internal boundaries implemented for 1.0.0rc1.
Public behavior is defined by the Python API and tests, not by private module
locations.

## Python package

The public facade modules are:

- `pyroxide.__init__`
- `pyroxide.plugins`
- `pyroxide.wasm`
- `pyroxide.types`
- `pyroxide.config`

Native implementation is split by responsibility:

- `_native_compile.py` owns compiler discovery, compilation locks, cache
  publication, and C, Rust, and Zig compiler invocation.
- `_native_plugins.py` owns native task decoration, library registration,
  metadata discovery, loading, and unregistration.
- `_ffi_proxy.py` owns FFI argument packing, result conversion, proxy methods,
  and batch adapters.

WebAssembly implementation is split similarly:

- `_wasm_compile.py` owns WAT registration and C, Rust, and Zig compilation.
- `_wasm_proxy.py` owns registration, task decoration, proxy methods, and
  loading.

`_async_waker.py` owns Unix pipe descriptors, pending asyncio futures, the
notification thread, registration, and cleanup. `types.py` retains the stable
`TaskHandle` API and delegates waker operations to that owner.

The facades explicitly re-export the established objects. They retain public
module identity for introspection and documentation. Private modules do not
import their public facade, which prevents circular ownership.

## Rust crate

- `lib.rs` declares modules and the extension entry point.
- `py_api.rs` validates Python inputs and exposes the PyO3 boundary.
- `broker.rs` owns admission, task storage, statistics, and lifecycle state.
- `task.rs` owns `Task`, `TaskStatus`, and typed `TaskKind`.
- `worker.rs` executes in-process work and coordinates isolated requests.
- `process_pool.rs` owns isolated worker processes and registry synchronization.
- `worker_process.rs` executes the isolated worker protocol.
- `registry.rs` owns versioned WebAssembly and native registrations.
- `backends/wasm.rs` and `backends/dylib/` own backend-specific execution.
- `ipc/` owns protocol metadata, frame I/O, and shared-memory lifetime.
- `async_waker.rs` owns the Rust side of Unix async notification.

## State ownership

There is one owner for each mutable subsystem:

- `Broker` owns tasks, queues, engine lifecycle, and statistics.
- The process pool owns child processes and their synchronized generations.
- Registry modules own registered WebAssembly bytes and native library paths.
- Python `ContextVar` objects own scoped configuration overrides.
- `_async_waker.py` owns Python waker state.
- `async_waker.rs` owns one cloned notification descriptor.
- `ShmemGuard` owns every Rust shared-memory mapping.

Shutdown follows ownership in reverse: stop admission, resolve or cancel
accepted tasks, stop workers, release isolated processes, resolve pending async
waiters, then close waker descriptors.

## Compatibility boundary

Private modules may be reorganized. The following must remain stable unless a
future release explicitly versions the change:

- documented imports and signatures;
- task and lifecycle semantics;
- serialization and exception behavior;
- IPC request and response bytes;
- native ABI contracts;
- supported version and package metadata.

See [IPC](ipc.md), [native ABI](native-abi.md), [unsafe code](unsafe-code.md),
and [development](development.md) for the corresponding maintainer contracts.
