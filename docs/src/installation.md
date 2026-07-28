# Installation

Pyroxide is published as `pyro3` and requires CPython 3.10 or newer.

```bash
python -m pip install pyro3
```

Release wheels target common Linux, macOS, and Windows platforms. If pip cannot
find a compatible wheel, it may try to build from source.

## Build from source

Source builds require Rust 1.86 or newer and `maturin`.

```bash
git clone https://github.com/emivvvvv/pyroxide.git
cd pyroxide
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
maturin develop
pytest -q
```

On Windows, activate the environment with `.venv\Scripts\activate`.

Runtime native or WASM compilation has additional compiler requirements. It is
optional; precompiled libraries and `.wasm` files do not need local compilers.
See [Native plugins](native_plugins.md) and [WebAssembly](wasm_engine.md).
