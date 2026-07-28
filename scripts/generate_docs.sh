#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python}"
MATURIN_BIN="${MATURIN_BIN:-maturin}"

command -v mdbook >/dev/null 2>&1 || {
    echo "mdbook is required to build the documentation" >&2
    exit 1
}
command -v "$MATURIN_BIN" >/dev/null 2>&1 || {
    echo "maturin is required to build the extension" >&2
    exit 1
}

"$MATURIN_BIN" develop
"$PYTHON_BIN" -m pdoc pyroxide -o docs/api --no-search
test -s docs/api/pyroxide.html

mdbook build docs
mkdir -p docs/book/api
cp -R docs/api/. docs/book/api/
test -s docs/book/api/pyroxide.html

echo "Documentation built in docs/book"
