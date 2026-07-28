# Contributing

Bug reports and focused pull requests are welcome. For security issues, follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.

## Setup

Use Python 3.10+ and Rust 1.86+.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
maturin develop
```

## Before submitting

```bash
pytest -q
ruff check python tests examples
mypy python/pyroxide
cargo fmt --all -- --check
cargo clippy --all-targets -- -D warnings
cargo test --all-targets
scripts/generate_docs.sh
```

Add a regression test for behavior changes. Keep public docs and type information
in sync with the implementation. Do not add performance claims without a script,
raw machine-readable results, platform metadata, repetitions, and a fair
comparison of equivalent execution semantics.

Keep changes small and explain compatibility or security impact in the pull
request. Update `CHANGELOG.md` for user-visible behavior.
