# RC1 Behavior-Preserving Architecture Refactor

**Date:** 2026-07-29
**Release:** 1.0.0rc1
**Status:** Approved

## Goal

Finish the internal architecture refactor before 1.0.0rc1 without changing the
supported public API, intended execution semantics, wire protocol, native ABI,
error contract, or lifecycle behavior. The final code should be easier to
review, test, and maintain while retaining the established reliability and
performance characteristics.

Confirmed defects discovered during the review are fixes, not new behavior.
In particular, stale async-waker cleanup must not unregister a newer file
descriptor, and malformed internal protocol input must fail closed.

## Compatibility contract

The following are release invariants:

1. Existing imports and documented public symbols remain available from the
   same modules.
2. Public call signatures, defaults, return types, exceptions, and
   serialization behavior remain stable.
3. Task submission, awaiting, cancellation, timeout, batching, worker
   recycling, crash recovery, and shutdown behavior remain stable.
4. Python, isolated Python, WebAssembly, and native-library execution retain
   the same supported behavior.
5. Existing IPC frame bytes and native ABI metadata remain compatible.
6. Context-scoped configuration remains isolated across threads and asyncio
   tasks.
7. The refactor introduces no repeatable regression in warm task latency,
   throughput, worker reliability, or sustained-operation accounting.

Internal implementation modules are private. Their layout may change as long
as the public facades and supported behavior remain stable.

## Approach

Use a staged mechanical extraction rather than a new runtime design. Lock down
behavior with characterization tests, move cohesive implementation units
behind the existing public facades, then remove duplication only after both
paths use the same tested helper.

Each stage must leave the repository testable. A failed characterization,
compatibility check, or benchmark comparison blocks the next stage until the
cause is understood.

## Python architecture

Keep these modules as the public facades:

- `pyroxide.plugins`
- `pyroxide.wasm`
- `pyroxide.types`
- `pyroxide.config`
- exports from `pyroxide.__init__`

Extract private responsibilities into focused modules:

- `_native_compile.py`: compiler discovery, source compilation, cache keys,
  and compiler command construction for supported native toolchains.
- `_native_plugins.py`: native-library loading, metadata validation, symbol
  binding, plugin registration, and plugin lifecycle helpers.
- `_ffi_proxy.py`: Python proxy objects and argument/result conversion for
  native FFI calls.
- `_wasm_compile.py`: WAT/WASM normalization, compilation, validation, and
  cache-related helpers.
- `_wasm_proxy.py`: WebAssembly callable proxies, argument/result conversion,
  and task-facing wrappers.
- `_async_waker.py`: Python-side waker registration, cleanup, and event-loop
  integration.

The public facades will import and re-export the same supported names. Private
modules must not create a second source of configuration or lifecycle state.
State ownership stays explicit, and dependencies point from public facade to
private implementation rather than between peer facades.

Pure moves come before cleanup. Renaming, deduplication, and tighter typing
follow only after moved code passes the existing and new characterization
tests.

## Rust architecture

Complete the refactor already started in the recent commits:

1. Keep `lib.rs` as a small module and extension entry point.
2. Keep `TaskKind` as the typed representation of execution mode.
3. Route WebAssembly and native-library scheduling through the shared
   execution path without changing backend-specific validation.
4. Make `ipc::protocol` the sole owner of typed request and response metadata,
   including length checks, flag validation, and byte encoding/decoding.
5. Make `ipc::frame` the sole owner of exact frame reads and writes.
6. Use the single `ipc::shmem::ShmemGuard` implementation for shared-memory
   cleanup.
7. Preserve the existing frame layout and reject unsupported flag bits rather
   than silently accepting malformed internal messages.
8. Fix async-waker cleanup by clearing only the file descriptor that the
   caller registered.

Unsafe blocks must be narrow and documented with the safety invariant at the
call site. Raw handles and borrowed buffers must not outlive their owners.

## Test strategy

Add or strengthen tests before changing each behavior-sensitive area:

- a deterministic stale-waker cleanup regression test;
- public import and signature characterization;
- proxy construction, representation, calling, and error behavior;
- plugin and WebAssembly compile/load/cache behavior;
- context-variable nesting, thread isolation, and asyncio isolation;
- IPC metadata round trips, truncation, overflow, and invalid flags;
- shared-memory cleanup on success and failure;
- worker crash, timeout, cancellation, recycling, and shutdown behavior;
- native ABI and WebAssembly boundary behavior.

Tests should assert externally observable contracts rather than private module
locations. Tests that specifically validate an internal safety invariant may
live next to the Rust implementation.

## Performance validation

Capture a clean baseline from the current pre-change commit before modifying
runtime code. Build the baseline and final code with the same toolchain,
interpreter, features, environment, and benchmark parameters.

Compare at least:

- warm in-process task overhead;
- warm isolated-task latency and throughput;
- WebAssembly boundary latency;
- native-library boundary latency;
- sustained isolated operation with deliberate worker failures;
- import and first-use cost where the Python module split could affect startup.

Use repeated runs and robust summaries instead of a single timing. Investigate
any repeatable regression above normal measurement noise. A repeatable
regression of more than three percent in a directly affected warm path blocks
RC1 unless it is explained, corrected, and explicitly accepted.

## Quality gates

Before declaring the refactor ready:

1. Rust formatting passes.
2. Rust checks, tests, and clippy pass with the correct feature mode for both
   the Python extension and standalone Rust test executable.
3. Python formatting/linting, type checking, and the full supported test suite
   pass.
4. Source distribution and wheel build from a clean archive.
5. The built wheel imports as 1.0.0rc1 in a fresh environment.
6. Public API and native ABI characterization passes.
7. Reliability and differential performance checks pass.
8. Release metadata and documentation agree on the version and distribution
   name.
9. The final worktree and diff contain no generated noise, credentials, or
   unrelated changes.

Platform-specific failures must be separated from product failures with
evidence. A release claim cannot rely on an unavailable platform or toolchain.

## Commit strategy

The work remains on local `main` as requested.

- Fixes and cleanup belonging to an existing local refactor commit will be
  folded into that commit while preserving its original message.
- A genuinely new architecture extraction may use one focused refactor
  commit.
- New architecture or safety documentation may use one focused documentation
  commit.
- Rewritten local commit hashes will be reviewed before any remote action.

No push, tag, release creation, package upload, or publication is authorized by
this design. Those actions require explicit approval after all quality gates
pass.

## Documentation

Add concise maintainership documentation for:

- module boundaries and state ownership;
- IPC frame and metadata ownership;
- native ABI assumptions and validation;
- unsafe-code invariants;
- local development and release verification commands.

The documentation must describe the implemented system, not an aspirational
design.

## Out of scope

- New public features or supported execution modes.
- Public API renames or removals.
- Wire-format or native ABI version changes.
- Performance optimizations that alter scheduling or fairness semantics.
- Publishing or changing remote state.

## Completion criteria

The refactor is complete only when the architecture above is implemented, the
compatibility contract is demonstrated by tests, differential performance is
within the stated threshold, packaging succeeds from a clean archive, and all
available release gates pass.

If any invariant cannot be demonstrated, the release remains blocked and the
unverified point is reported explicitly.
