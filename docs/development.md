# Development and RC Verification

Pyroxide requires Python 3.10 or newer and Rust 1.86 or newer. Run release
checks with Rust 1.86 because that is the declared minimum supported toolchain.

## Environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,benchmark]'
maturin develop
```

When another `.venv` exists in the repository, ensure `maturin develop` uses
the intended active environment. A Python 3.14t extension must not be loaded
into a standard Python 3.11 process.

## Static quality gates

```bash
ruff check python tests examples
mypy python/pyroxide
cargo fmt --all -- --check
cargo check --all-targets
cargo clippy --all-targets -- -D warnings
```

PyO3's `extension-module` feature intentionally does not link libpython.
Standalone Rust test executables must therefore disable the default extension
feature:

```bash
cargo test --no-default-features --all-targets
```

Extension builds continue to use the feature selected by `pyproject.toml`.

## Python and packaging gates

```bash
python -m pytest -q
maturin build --release --out dist
maturin sdist --out dist
```

Install the wheel into a fresh environment. Verify `pyroxide.__version__`,
then smoke-test synchronous, asynchronous, isolated Python, WebAssembly, and
available native execution.

Build release artifacts from `git archive` rather than a dirty worktree. This
proves that every required source file is tracked and generated local files
are not masking packaging errors.

## Documentation

```bash
PYTHON_BIN=.venv/bin/python \
MATURIN_BIN=.venv/bin/maturin \
scripts/generate_docs.sh
```

Maintain [architecture](architecture.md), [IPC](ipc.md),
[native ABI](native-abi.md), and [unsafe code](unsafe-code.md) when their
corresponding implementation changes.

## Performance and reliability

Compare a clean baseline and final build with the same interpreter, Rust
toolchain, environment variables, payloads, worker limits, warmups, and
repetition count. Save raw machine-readable samples.

Investigate any repeatable regression above normal measurement noise. For the
1.0.0rc1 refactor, a repeatable regression above three percent in a directly
affected warm path blocks readiness.

Reliability runs must account for every accepted operation and finish with no
assertion failures, incorrect results, leaked active work, or failed recovery
checks.

## Publication hold

Passing local checks does not authorize publication. Before a release:

1. verify version agreement across `pyproject.toml`, `Cargo.toml`, and
   `pyroxide.__version__`;
2. verify a clean worktree and intended local history;
3. verify no release tag already points at the commit;
4. obtain explicit approval before pushing, tagging, uploading, or publishing.
