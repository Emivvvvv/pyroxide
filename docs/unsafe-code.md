# Unsafe Code Policy

Pyroxide uses unsafe Rust only at operating-system, shared-memory, WebAssembly,
and native ABI boundaries. Safe scheduling and lifecycle code must not depend
on unchecked pointers.

## Required review standard

Every unsafe block has a nearby `SAFETY` comment that states the concrete
invariant. Every unsafe function documents the caller contract at module or
function scope.

Reviewers must verify:

1. the pointer or handle origin;
2. the valid byte range and lifetime;
3. aliasing and mutability;
4. ABI and function signature compatibility;
5. ownership and exactly-once cleanup;
6. failure behavior before any dereference or call.

## Unsafe categories

### Operating-system calls

Unix uses `poll`, `write`, `getppid`, `kill`, kqueue, and `shm_unlink`.
Windows uses process handle APIs. File descriptors and handles must be live for
the call, initialized buffers must match their declared counts, and acquired
handles must be closed exactly once.

### Async waker descriptor

Python owns the source pipe descriptor. Rust borrows it only long enough to
clone an `OwnedFd`. Rust stores the source descriptor number for matching
cleanup and writes only through the owned clone.

### Shared memory

`ShmemGuard` owns each mapping. Slice construction uses the mapping pointer and
exact mapping length. Copies require an equal source and destination length.
The receiver unlinks the name after opening on Unix, while open handles keep
the mapping alive.

### WebAssembly memory

Guest pointer and length values are converted with checked arithmetic and
validated against guest memory before creating host slices or copying bytes.
Host slices never outlive the active store and memory mapping.

### Native libraries

Native symbol lookup and calls require trusted libraries. The library owner
outlives cached pointers. Raw output is null-checked and length-bounded before
copying, then freed exactly once.

Prepared FFI calls validate primitive types, argument count, payload length,
optional metadata, and return width before transmuting or calling a function
pointer.

## Audit commands

```bash
rg -n 'unsafe( \{| fn | extern)' src
rg -n -B 4 'unsafe \{' src
cargo clippy --all-targets -- -D warnings
cargo test --no-default-features --all-targets
```

An unsafe change requires focused boundary tests plus the complete Rust and
relevant Python execution suites.
