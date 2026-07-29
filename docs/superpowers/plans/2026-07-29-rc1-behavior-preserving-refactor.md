# RC1 Behavior-Preserving Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the internal Rust and Python architecture refactor while preserving every supported 1.0.0rc1 API, execution contract, wire byte, native ABI, reliability invariant, and directly affected performance path.

**Architecture:** Keep existing Python modules as stable public facades and move cohesive implementation into private modules. Complete the Rust IPC ownership split so protocol types own headers and flags, frame helpers own exact I/O, and one guard owns shared-memory cleanup. Use characterization and regression tests before each behavior-sensitive change, then compare the final build with a clean pre-change baseline.

**Tech Stack:** Rust 1.86, PyO3 0.29, Python 3.10 through 3.14, pytest, Ruff 0.16.0, mypy 2.3.0, maturin, mdBook, Git.

## Global Constraints

- Preserve documented imports, public signatures, defaults, return types, exceptions, serialization, IPC bytes, and native ABI metadata.
- Preserve task submission, awaiting, cancellation, timeout, batching, recycling, crash recovery, and shutdown behavior.
- Keep `pyroxide.plugins`, `pyroxide.wasm`, `pyroxide.types`, `pyroxide.config`, and `pyroxide.__init__` as public facades.
- A repeatable regression above three percent in a directly affected warm path blocks RC1.
- Work on local `main`; do not push, tag, create a release, upload a package, or publish.
- Fold fixes into the relevant existing local commits with their original messages. Use new commits only for genuinely new refactor or documentation units.
- Do not add author, assistant, or generated-by attribution.

---

## File Structure

### Python files to create

- `python/pyroxide/_native_compile.py`: native compiler discovery, locking, command execution, cache publication, and Rust/C/Zig compilation.
- `python/pyroxide/_native_plugins.py`: native task decorator, library loading, registration, metadata discovery, and unregistration.
- `python/pyroxide/_ffi_proxy.py`: `DylibProxy` and FFI packing/result conversion.
- `python/pyroxide/_wasm_compile.py`: WAT/C/Rust/Zig to WebAssembly compilation.
- `python/pyroxide/_wasm_proxy.py`: `WasmProxy`, task decorator, registration, and loading.
- `python/pyroxide/_async_waker.py`: pipe state, registration, notification thread, cleanup, and event-loop future resolution.

### Python facades to modify

- `python/pyroxide/plugins.py`: import and re-export the existing native public API.
- `python/pyroxide/wasm.py`: import and re-export the existing WebAssembly public API.
- `python/pyroxide/types.py`: retain `TaskHandle` and delegate async-waker behavior.
- `python/pyroxide/__init__.py`: retain the existing shutdown and export behavior.

### Rust files to modify

- `src/async_waker.rs`: compare the registered descriptor during cleanup and add the regression test.
- `src/ipc/protocol.rs`: typed request/response headers, supported flags, validation, and byte-preserving codecs.
- `src/ipc/frame.rs`: exact request/response frame I/O using protocol types.
- `src/ipc/shmem.rs`: sole shared-memory owner and documented unsafe invariants.
- `src/ipc/mod.rs`: narrow internal exports.
- `src/worker.rs`: use typed request/response framing and the shared guard.
- `src/worker_process.rs`: remove the duplicate guard and use typed framing.
- `src/process_pool.rs`: use typed framing for registry synchronization.
- `src/config.rs`, `src/registry.rs`, `src/task.rs`, `src/py_api.rs`, and `src/lib.rs`: formatting and safety documentation only unless a failing test demonstrates a required correction.

### Tests and release files to modify

- `tests/test_async_completion.py`: public async-waker reinitialization and stale cleanup contract.
- `tests/test_characterization_api.py`: stable exports, defining modules, and signatures.
- `tests/test_characterization_ffi.py`: native facade/proxy/decorator behavior.
- `tests/test_characterization_wasm.py`: WebAssembly facade/proxy/decorator behavior.
- `tests/test_characterization_config.py`: context isolation behavior and lint cleanup.
- `tests/test_characterization_lifecycle.py`: lifecycle behavior and lint cleanup.
- `.github/workflows/ci.yml`: standalone Rust tests without `extension-module`.
- `.github/workflows/release.yml`: tagged-source Rust tests without `extension-module`.
- `examples/benchmarks/worker.py`, `python/pyroxide/config.py`, and `tests/test_config.py`: existing Ruff violations only.
- `docs/architecture.md`, `docs/ipc.md`, `docs/native-abi.md`, `docs/unsafe-code.md`, and `docs/development.md`: implemented architecture and verification guidance.

---

### Task 1: Capture the clean pre-change baseline

**Files:**
- Read: `examples/benchmarks/manifests/smoke.toml`
- Read: `examples/benchmarks/manifests/plugin-boundaries.toml`
- Read: `examples/benchmarks/reliability_runner.py`
- Artifact outside repository: `/tmp/pyroxide-rc1-baseline`

**Interfaces:**
- Consumes: Git commit `17cb28a` whose runtime tree matches the reviewed pre-change `81e43ae`.
- Produces: immutable baseline source archive, wheel, API snapshot, test results, and repeated timing JSON for final comparison.

- [ ] **Step 1: Record repository and toolchain identity**

Run:

```bash
git rev-parse HEAD
rustc --version
cargo --version
python3.11 --version
uv --version
```

Expected: HEAD is `17cb28a` or its documentation-only descendant, Rust is 1.86-compatible, and Python 3.11 is available.

- [ ] **Step 2: Export an immutable clean baseline**

Run:

```bash
mkdir -p /tmp/pyroxide-rc1-baseline
git archive --format=tar HEAD | tar -xf - -C /tmp/pyroxide-rc1-baseline
```

Expected: `/tmp/pyroxide-rc1-baseline/Cargo.toml` and `pyproject.toml` exist without worktree-only files.

- [ ] **Step 3: Build and test the baseline wheel**

Run in `/tmp/pyroxide-rc1-baseline`:

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python "maturin>=1,<2" "pytest>=8" "pyperf>=2.7,<3" "psutil>=6.1,<8"
.venv/bin/maturin develop --release
.venv/bin/python -m pytest -q tests/test_characterization_api.py tests/test_characterization_config.py tests/test_characterization_ffi.py tests/test_characterization_lifecycle.py tests/test_characterization_wasm.py
```

Expected: characterization suite passes.

- [ ] **Step 4: Save public API and import-time snapshots**

Run a Python script that serializes `inspect.signature` and `__module__` for every exported callable in `pyroxide`, `pyroxide.plugins`, `pyroxide.wasm`, `pyroxide.types`, and `pyroxide.config`, then records 30 fresh interpreter import timings to `/tmp/pyroxide-rc1-baseline/api.json` and `/tmp/pyroxide-rc1-baseline/import-times.json`.

Expected: both JSON files parse and contain the runtime version `1.0.0rc1`.

- [ ] **Step 5: Save repeated directly affected timing samples**

Use the existing smoke and plugin-boundary benchmark controllers with identical fixed manifests and five repetitions. Save raw JSONL under `/tmp/pyroxide-rc1-baseline/results/`.

Expected: every measured operation returns its expected value, all accepted reliability operations are accounted for, and raw samples exist for final statistical comparison.

---

### Task 2: Fix stale async-waker cleanup and Rust test feature mode

**Files:**
- Modify: `src/async_waker.rs`
- Test: `src/async_waker.rs`
- Test: `tests/test_async_completion.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `set_async_waker_fd(fd: RawFd) -> Result<(), String>`.
- Produces: `clear_async_waker_fd(fd: RawFd) -> bool` that clears only the matching registered descriptor.

- [ ] **Step 1: Add the failing stale-descriptor Rust regression**

Add a Unix-only test that registers one `UnixStream` writer, replaces it with a second writer, calls `clear_async_waker_fd` with the stale first descriptor, calls `notify_waker(1)`, and asserts the second reader receives one byte.

Core assertion:

```rust
assert!(!clear_async_waker_fd(first_writer.as_raw_fd()));
notify_waker(1);
second_reader.read_exact(&mut byte).unwrap();
assert_eq!(byte, [1]);
```

- [ ] **Step 2: Run the regression and verify RED**

Run:

```bash
cargo test --no-default-features async_waker::tests::stale_clear_preserves_replacement -- --exact
```

Expected: FAIL because the stale clear currently removes the replacement descriptor.

- [ ] **Step 3: Implement matching cleanup**

Store the caller's descriptor number next to the owned clone. Under the existing
mutex, compare that source descriptor with the caller's `fd`. Call `take()` only
on equality and return whether a descriptor was removed.

```rust
struct RegisteredWaker {
    source_fd: RawFd,
    owned_fd: OwnedFd,
}

if guard
    .as_ref()
    .is_some_and(|registered| registered.source_fd == fd)
{
    guard.take();
    true
} else {
    false
}
```

- [ ] **Step 4: Run focused and Python async tests**

Run:

```bash
cargo test --no-default-features async_waker::tests::stale_clear_preserves_replacement -- --exact
python -m pytest -q tests/test_async_completion.py tests/test_audit_improvements.py -k waker
```

Expected: focused Rust regression and existing Python waker contracts pass.

- [ ] **Step 5: Correct standalone Rust test commands**

Change CI and tagged-source test commands from `cargo test --all-targets` to:

```bash
cargo test --no-default-features --all-targets
```

Keep extension builds on the `extension-module` feature.

- [ ] **Step 6: Create a fixup commit**

Stage only the waker, async tests, and workflow changes. Create a fixup targeting `88f87bf` for the waker code and a separate fixup targeting `a400d78` for the feature-mode workflow correction.

---

### Task 3: Complete typed IPC and shared-memory ownership

**Files:**
- Modify: `src/ipc/protocol.rs`
- Modify: `src/ipc/frame.rs`
- Modify: `src/ipc/shmem.rs`
- Modify: `src/ipc/mod.rs`
- Modify: `src/worker.rs`
- Modify: `src/worker_process.rs`
- Modify: `src/process_pool.rs`

**Interfaces:**
- Produces: `RequestHeader::new`, `RequestHeader::encode`, `RequestHeader::decode`.
- Produces: `ResponseHeader::new`, `ResponseHeader::encode`, `ResponseHeader::decode`.
- Produces: `FrameFlags::inline`, `FrameFlags::shared_memory`, `FrameFlags::decode`.
- Produces: `read_request`, `write_request`, `read_response`, and `write_response` frame helpers.
- Preserves: request bytes `[kind:u8][flags:u8][metadata_len:u32 BE][payload_len:u64 BE]`.
- Preserves: response bytes `[success:u8][flags:u8][payload_len:u64 BE]`.

- [ ] **Step 1: Add failing protocol validation tests**

Add tests asserting:

```rust
assert!(FrameFlags::decode(0).is_ok());
assert!(FrameFlags::decode(1).is_ok());
assert!(FrameFlags::decode(2).is_err());
assert!(ResponseHeader::decode([1, 2, 0, 0, 0, 0, 0, 0, 0, 0]).is_err());
```

Add exact expected byte arrays for one inline Python request and one shared-memory success response.

- [ ] **Step 2: Run protocol tests and verify RED**

Run:

```bash
cargo test --no-default-features ipc::protocol::tests -- --nocapture
```

Expected: compile failure because the typed header and flag interfaces do not exist.

- [ ] **Step 3: Implement typed headers and flags**

Define private typed headers with checked constructors. Encode to fixed arrays and decode only `0` or `1` for boolean/status bytes and only bit zero for shared memory. Use `checked_ipc_len` for every decoded length.

```rust
pub(crate) struct RequestHeader {
    pub(crate) kind: u8,
    pub(crate) flags: FrameFlags,
    pub(crate) metadata_len: usize,
    pub(crate) payload_len: usize,
}

pub(crate) struct ResponseHeader {
    pub(crate) success: bool,
    pub(crate) flags: FrameFlags,
    pub(crate) payload_len: usize,
}
```

- [ ] **Step 4: Run protocol tests and verify GREEN**

Run:

```bash
cargo test --no-default-features ipc::protocol::tests -- --nocapture
```

Expected: round trips, exact byte tests, truncation, mismatch, and invalid-flag tests pass.

- [ ] **Step 5: Add failing in-memory frame tests**

Use `std::io::Cursor<Vec<u8>>` to assert `write_request` produces the old wire bytes, `read_request` reconstructs the same metadata and payload, truncated payloads fail, and unsupported flag bits fail before allocation.

- [ ] **Step 6: Run frame tests and verify RED**

Run:

```bash
cargo test --no-default-features ipc::frame::tests -- --nocapture
```

Expected: compile failure because the typed frame helpers do not exist.

- [ ] **Step 7: Implement exact frame helpers**

Move request and response header/payload reads and writes into `ipc::frame`.
Keep cancellation-aware nonblocking response-header reading in `worker.rs`, but
decode the completed fixed header through `ResponseHeader`. Return `Ok(None)`
only when a worker sees clean EOF before the first request header byte so the
existing graceful shutdown behavior remains unchanged.

```rust
pub(crate) fn read_request(
    stream: &mut impl Read,
) -> Result<Option<(RequestMetadata, FrameFlags, Vec<u8>)>, String>;

pub(crate) fn write_request(
    stream: &mut impl Write,
    metadata: &RequestMetadata,
    flags: FrameFlags,
    payload: &[u8],
) -> Result<(), String>;

pub(crate) fn read_response(
    stream: &mut impl Read,
) -> Result<(ResponseHeader, Vec<u8>), String>;

pub(crate) fn write_response(
    stream: &mut impl Write,
    success: bool,
    flags: FrameFlags,
    payload: &[u8],
) -> Result<(), String>;
```

- [ ] **Step 8: Replace duplicate call sites**

Use the frame helpers in registration synchronization and the worker loop. Remove the private `worker_process::ShmemGuard`, import `crate::ipc::ShmemGuard`, and use its `create`, `open`, slice, and pointer methods.

- [ ] **Step 9: Run Rust and isolated-process regression suites**

Run:

```bash
cargo test --no-default-features --all-targets
python -m pytest -q tests/test_isolated.py tests/test_cancellation.py tests/test_lifecycle.py tests/test_memory.py tests/test_panic_safety.py tests/test_security_remediation.py
```

Expected: all available tests pass and no wire-format snapshot changes.

- [ ] **Step 10: Create a fixup commit**

Create a fixup targeting `d9f653a` so the completed IPC ownership work retains the original `refactor(rust): centralize ipc protocol and frame helpers` message after autosquash.

---

### Task 4: Extract native compiler, registry, and FFI proxy modules

**Files:**
- Create: `python/pyroxide/_native_compile.py`
- Create: `python/pyroxide/_native_plugins.py`
- Create: `python/pyroxide/_ffi_proxy.py`
- Modify: `python/pyroxide/plugins.py`
- Test: `tests/test_characterization_api.py`
- Test: `tests/test_characterization_ffi.py`
- Test: `tests/test_plugins.py`
- Test: `tests/test_compiler_validation.py`
- Test: `tests/test_ffi_expanded.py`
- Test: `tests/test_c_zig_plugins.py`

**Interfaces:**
- `plugins.py` continues to export `CrossProcessLock`, `CompilerNotFoundError`, `compile_rust`, `compile_c`, `compile_zig`, `dylib_task`, `DylibProxy`, `load_dylib`, and `unregister_dylib`.
- `_native_compile.py` consumes registration through a callable passed by `_native_plugins.py`; it must not import the public facade.
- `_ffi_proxy.py` consumes the raw submit functions, `TaskHandle`, FFI format helpers, and scoped queue timeout getter.

- [ ] **Step 1: Extend characterization before moving code**

Assert the public symbols above are identical imports from `pyroxide.plugins`, signatures match the baseline JSON, generated proxy class names remain unchanged, FFI `.batch` attributes exist, and compiler exceptions retain their exact classes and message fragments.

- [ ] **Step 2: Run native characterization**

Run:

```bash
python -m pytest -q tests/test_characterization_api.py tests/test_characterization_ffi.py tests/test_plugins.py tests/test_compiler_validation.py tests/test_ffi_expanded.py
```

Expected: PASS before extraction, proving the characterization describes current behavior.

- [ ] **Step 3: Extract compilation implementation**

Move compiler locks, cache publication, compiler discovery, compilation policy, and the three compile functions into `_native_compile.py`. Keep their function objects re-exported by `plugins.py`; do not add wrapper call frames.

```python
from ._native_compile import (
    CompilerNotFoundError,
    CrossProcessLock,
    compile_c,
    compile_rust,
    compile_zig,
)
```

- [ ] **Step 4: Run compiler tests**

Run:

```bash
python -m pytest -q tests/test_compiler_validation.py tests/test_plugins.py tests/test_c_zig_plugins.py
```

Expected: same pass/skip results as the baseline.

- [ ] **Step 5: Extract FFI proxy implementation**

Move `DylibProxy` and its packing/result helpers to `_ffi_proxy.py`. Inject or import only private raw bindings and `TaskHandle`; preserve dynamic proxy subclass names and method attributes.

```python
from ._ffi_proxy import DylibProxy
```

- [ ] **Step 6: Run FFI tests**

Run:

```bash
python -m pytest -q tests/test_characterization_ffi.py tests/test_ffi_expanded.py tests/test_plugins.py
```

Expected: all tests pass with unchanged exception types and results.

- [ ] **Step 7: Extract registration and decorator implementation**

Move `dylib_task`, `load_dylib`, and `unregister_dylib` to `_native_plugins.py`. Make `plugins.py` a documented facade containing explicit imports and `__all__`.

```python
from ._native_plugins import dylib_task, load_dylib, unregister_dylib

__all__ = [
    "CompilerNotFoundError",
    "CrossProcessLock",
    "DylibProxy",
    "compile_c",
    "compile_rust",
    "compile_zig",
    "dylib_task",
    "load_dylib",
    "unregister_dylib",
]
```

- [ ] **Step 8: Run the complete native suite**

Run:

```bash
python -m pytest -q tests/test_characterization_api.py tests/test_characterization_ffi.py tests/test_plugins.py tests/test_compiler_validation.py tests/test_ffi_expanded.py tests/test_c_zig_plugins.py
```

Expected: baseline-equivalent results.

- [ ] **Step 9: Commit the new extraction**

Create one new commit:

```text
refactor(python): separate native compilation and ffi proxies
```

---

### Task 5: Extract WebAssembly compiler and proxy modules

**Files:**
- Create: `python/pyroxide/_wasm_compile.py`
- Create: `python/pyroxide/_wasm_proxy.py`
- Modify: `python/pyroxide/wasm.py`
- Test: `tests/test_characterization_api.py`
- Test: `tests/test_characterization_wasm.py`
- Test: `tests/test_wasm.py`

**Interfaces:**
- `wasm.py` continues to export `register_wasm`, `register_wasm_wat`, `wasm_task`, `WasmProxy`, `load_wasm`, `compile_wat_wasm`, `compile_c_wasm`, `compile_rust_wasm`, `compile_zig_wasm`, and `compile_wasm`.
- `_wasm_compile.py` consumes registration via the private raw binding and does not import the public facade.
- `_wasm_proxy.py` consumes raw submission, scoped limits, and `TaskHandle`.

- [ ] **Step 1: Extend WebAssembly characterization**

Assert stable signatures, dynamic proxy class names, task decorator `.batch`, scoped timeout/memory propagation, unsupported-language errors, and compile-disabled errors.

- [ ] **Step 2: Run WebAssembly characterization**

Run:

```bash
python -m pytest -q tests/test_characterization_wasm.py tests/test_wasm.py
```

Expected: PASS before extraction.

- [ ] **Step 3: Extract compile functions**

Move compilation guard and WAT/C/Rust/Zig compilation into `_wasm_compile.py`. Re-export the original function objects from `wasm.py`.

```python
from ._wasm_compile import (
    compile_c_wasm,
    compile_rust_wasm,
    compile_wasm,
    compile_wat_wasm,
    compile_zig_wasm,
)
```

- [ ] **Step 4: Run compile-path tests**

Run:

```bash
python -m pytest -q tests/test_characterization_wasm.py tests/test_wasm.py -k 'compile or register'
```

Expected: baseline-equivalent pass/skip results.

- [ ] **Step 5: Extract proxy and task functions**

Move registration, `wasm_task`, `WasmProxy`, and `load_wasm` into `_wasm_proxy.py`. Make `wasm.py` an explicit facade with `__all__`.

```python
from ._wasm_proxy import (
    WasmProxy,
    load_wasm,
    register_wasm,
    register_wasm_wat,
    wasm_task,
)
```

- [ ] **Step 6: Run the complete WebAssembly suite**

Run:

```bash
python -m pytest -q tests/test_characterization_api.py tests/test_characterization_wasm.py tests/test_wasm.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit the extraction**

Create one new commit:

```text
refactor(python): separate wasm compilation and proxies
```

---

### Task 6: Extract async-waker state from `types.py`

**Files:**
- Create: `python/pyroxide/_async_waker.py`
- Modify: `python/pyroxide/types.py`
- Modify: `python/pyroxide/__init__.py`
- Test: `tests/test_async_completion.py`
- Test: `tests/test_characterization_lifecycle.py`
- Test: `tests/test_audit_improvements.py`

**Interfaces:**
- Produces: `_async_waker.ensure_waker_registered(loop)`.
- Produces: `_async_waker.cleanup_waker()`.
- `types.py` retains compatibility aliases `ensure_waker_registered` and `_cleanup_waker`.
- `TaskHandle.result_async` keeps the same signature and result behavior.

- [ ] **Step 1: Add facade compatibility tests**

Assert `pyroxide.types.ensure_waker_registered` and `pyroxide.types._cleanup_waker` remain callable, shutdown invokes cleanup, a stale cleanup cannot remove a replacement registration, and `TaskHandle.result_async()` resolves after reinitialization.

- [ ] **Step 2: Run async tests before extraction**

Run:

```bash
python -m pytest -q tests/test_async_completion.py tests/test_characterization_lifecycle.py tests/test_audit_improvements.py -k 'async or waker or shutdown'
```

Expected: existing contracts pass; the stale-cleanup test passes after Task 2.

- [ ] **Step 3: Move waker ownership**

Move all waker globals and functions into `_async_waker.py`. Import `ensure_waker_registered` and alias `cleanup_waker as _cleanup_waker` in `types.py`. Keep shutdown importing the compatibility alias or the private owner consistently.

```python
from ._async_waker import cleanup_waker as _cleanup_waker
from ._async_waker import ensure_waker_registered
```

- [ ] **Step 4: Run async and lifecycle suites**

Run:

```bash
python -m pytest -q tests/test_async_completion.py tests/test_characterization_lifecycle.py tests/test_lifecycle.py tests/test_audit_improvements.py
```

Expected: all tests pass without leaked waker threads or descriptors.

- [ ] **Step 5: Commit the extraction**

Create one new commit:

```text
refactor(python): isolate async waker lifecycle
```

---

### Task 7: Complete safety, architecture, and quality documentation

**Files:**
- Create: `docs/architecture.md`
- Create: `docs/ipc.md`
- Create: `docs/native-abi.md`
- Create: `docs/unsafe-code.md`
- Create: `docs/development.md`
- Modify: Rust files containing `unsafe`
- Modify: Python/test/example files reported by Ruff

**Interfaces:**
- Documents the implemented module dependency direction, wire bytes, native metadata limits, unsafe invariants, and exact local release checks.

- [ ] **Step 1: Audit every unsafe block**

Run:

```bash
rg -n 'unsafe( \\{| fn | extern)' src
```

For each result, verify pointer lifetime, buffer length, aliasing, handle ownership, callback ABI, or operating-system precondition. Add a `// SAFETY:` comment immediately above each block or expression with the concrete invariant.

- [ ] **Step 2: Format and run Rust gates**

Run:

```bash
cargo fmt --all
cargo fmt --all -- --check
cargo check --all-targets
cargo clippy --all-targets -- -D warnings
cargo test --no-default-features --all-targets
```

Expected: every command exits zero.

- [ ] **Step 3: Fix only reported Ruff issues**

Run:

```bash
ruff check python tests examples --fix
ruff check python tests examples
mypy python/pyroxide
```

Review every automatic change. Retain only import ordering and unused-import cleanup that does not alter runtime initialization.

- [ ] **Step 4: Write implemented-system documentation**

Document:

- public facade to private implementation dependency direction;
- task and registry state ownership;
- exact request and response byte layouts and supported flag bit;
- shared-memory create/open/unlink lifetime;
- native ABI ownership and maximum eight primitive arguments;
- each unsafe category and its invariant;
- extension build versus standalone Rust test feature commands;
- the no-publish release verification sequence.

- [ ] **Step 5: Validate documentation**

Run:

```bash
rg -n '[–—]' README.md docs --glob '*.md'
PYTHON_BIN=.venv/bin/python MATURIN_BIN=.venv/bin/maturin scripts/generate_docs.sh
git diff --check
```

Expected: no forbidden dash characters in new documentation, documentation builds, and the diff has no whitespace errors.

- [ ] **Step 6: Commit documentation and fold cleanup**

Fold Rust formatting/safety comments into `a347900`, Python facade lint into the relevant extraction commits, and characterization lint into `16a02ca`. Create one new documentation commit:

```text
docs(architecture): document runtime boundaries and safety
```

---

### Task 8: Autosquash local history and perform RC1 verification

**Files:**
- Verify: entire repository
- Compare: `/tmp/pyroxide-rc1-baseline`

**Interfaces:**
- Produces: compact local history, clean source archive, RC1 readiness evidence, and an explicit publication hold.

- [ ] **Step 1: Autosquash fixup commits**

Run an interactive autosquash rebase from `origin/main` with the sequence editor set to accept the generated order. Resolve conflicts by preserving the tested final tree and every original target message.

Expected: fixups disappear, original commit messages remain, new hashes are local only, and new extraction/documentation commits remain focused.

- [ ] **Step 2: Re-run static gates from the rewritten tree**

Run:

```bash
cargo fmt --all -- --check
cargo check --all-targets
cargo clippy --all-targets -- -D warnings
cargo test --no-default-features --all-targets
ruff check python tests examples
mypy python/pyroxide
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 3: Run the supported Python suite**

Build the extension in a clean Python 3.11 environment and run:

```bash
python -m pytest -q
```

Classify unavailable compiler/platform benchmark setup separately. Any core product failure blocks RC1.

- [ ] **Step 4: Build clean release artifacts**

Export the rewritten HEAD with `git archive`, create a fresh environment, build wheel and sdist, install the wheel, verify `pyroxide.__version__ == "1.0.0rc1"`, run a synchronous task, an async task, isolated Python, WebAssembly, and available native smoke tests.

- [ ] **Step 5: Compare API snapshots**

Generate the same JSON snapshot as Task 1. Compare public names, signatures, defaults, exception classes, and documented module imports. Dynamic implementation `__module__` differences are allowed only for private implementation classes whose public import path and pickling contract remain verified.

- [ ] **Step 6: Compare performance**

Run the same manifests, interpreter, toolchain, fixed environment, repetition count, and payloads as Task 1. Compare medians and dispersion. Re-run any path slower by more than three percent to distinguish noise from a repeatable regression.

Expected: no directly affected warm path has a repeatable regression above three percent; correctness and reliability invariants remain exact.

- [ ] **Step 7: Review final history and worktree**

Run:

```bash
git status --short --branch
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
git tag --points-at HEAD
```

Expected: clean worktree, compact intentional history, no release tag, and no remote mutation.

- [ ] **Step 8: Report readiness and stop**

Report exact verification evidence, any unavailable platform coverage, performance comparison, final commits, and whether RC1 is ready. Do not push, tag, upload, or publish. Wait for explicit publication approval.
