# Changelog

This project follows semantic versioning. Release-candidate behavior may still
change before final 1.0 when required to correct a safety or compatibility issue.

## [1.0.0rc1] - Unreleased

### Added

- Public, idempotent `shutdown()` with drain and pending-cancellation options.
- Fork-use detection with `ForkSafetyError`.
- Queue capacity, queued/running/rejected metrics, and isolated-process limits.
- Python 3.14 free-threaded test and wheel coverage.
- Typed-package marker and Python 3.10 stable-ABI wheels.
- Release tag/version validation and native wheel smoke tests before publishing.

### Changed

- Minimum supported Python is 3.10; minimum Rust for source builds is 1.86.
- Wasmtime was upgraded to a patched release and built without unused WASI,
  component-model, profiling, pooling, or Winch features.
- Batch admission is all-or-nothing and does not leave rejected task records.
- Running in-process cancellation now returns `False` and preserves the real
  result. Running isolated cancellation waits for worker termination.
- Isolated execution uses a bounded coordinator pool.
- Runtime configuration rejects invalid values at startup.
- Native compiler cache publication is atomic and compiler commands have timeouts.
- Fork detection covers broker and WebAssembly initialization.
- `shutdown(wait=True)` rejects calls from a Pyroxide worker to prevent self-join;
  worker tasks may initiate shutdown with `wait=False`.
- License options are now MIT or Apache-2.0.

### Security

- Validated WASM input/output lengths, signed offsets, range overflow, and guest
  memory bounds before host allocation or access.
- Bounded IPC metadata and frame allocations.
- Restricted Unix IPC directories to the current user and improved worker cleanup.
- Added an option to disable runtime source compilation in production.

[1.0.0rc1]: https://github.com/emivvvvv/pyroxide/compare/v0.10.0...v1.0.0rc1
