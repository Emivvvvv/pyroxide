#!/bin/bash
set -e

# Ensure we are in the repository root directory
cd "$(dirname "$0")/.."

echo "=== Building Pyroxide Extension Module ==="
.venv/bin/maturin develop

echo "=== Generating pdoc Python API Reference ==="
.venv/bin/pip install -q pdoc
.venv/bin/pdoc pyroxide -o docs/api --no-search

echo "=== Generating mdBook Documentation ==="
if command -v mdbook &> /dev/null; then
    mdbook build docs
    echo "mdBook built successfully in docs/book/"
else
    echo "WARNING: mdbook binary not found. Please install it with 'cargo install mdbook'."
fi

echo "=== Documentation generated successfully ==="
