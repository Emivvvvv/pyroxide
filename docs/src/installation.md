# Installation

Pyroxide is distributed on PyPI as `pyro3`. It bundles pre-compiled binary wheels for Linux, macOS (Apple Silicon/Intel), and Windows.

## 1. Installing from PyPI

Install the pre-compiled library directly into your virtual environment:

```bash
pip install pyro3
```

## 2. Building from Source

To compile Pyroxide locally, you will need a Rust compiler (minimum Rust 1.70+) and `maturin` (the PyO3 compilation backend).

```bash
# 1. Install Rust (if not already installed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# 2. Clone the repository
git clone https://github.com/emivvvvv/pyroxide.git
cd pyroxide

# 3. Create and activate a Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 4. Install compilation requirements
pip install maturin pytest ruff

# 5. Compile and install in development/editable mode
maturin develop
```
