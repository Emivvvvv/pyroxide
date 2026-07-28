# Installation

Pyroxide is published on PyPI as `pyro3` and imported as `pyroxide`. It requires
CPython 3.10 or newer.

```bash
python -m pip install pyro3
```

Then confirm the installed release:

```bash
python -c "import pyroxide; print(pyroxide.__version__)"
```

## Wheels

Release wheels target common Linux, macOS, and Windows platforms. Pip uses a
compatible wheel when one is available, so installing Pyroxide does not
normally require a Rust toolchain.

Free-threaded CPython uses dedicated wheels. Regular CPython wheels use the
stable ABI where the platform supports it.

## Build from source

A source build requires Rust 1.86 or newer and `maturin`:

```bash
git clone https://github.com/emivvvvv/pyroxide.git
cd pyroxide
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
maturin develop
pytest -q
```

On Windows, activate the environment with:

```powershell
.venv\Scripts\activate
```

See [CONTRIBUTING.md](https://github.com/emivvvvv/pyroxide/blob/main/CONTRIBUTING.md)
for the complete development workflow.

## Optional compilers

Ordinary Python tasks, precompiled native libraries, and precompiled `.wasm`
files do not need local C, Zig, Rust-to-WASM, or Emscripten compilers.

Install those toolchains only if the application intentionally compiles trusted
native or WASM source at runtime. The [native plugin](native_plugins.md) and
[WASM](wasm_engine.md) chapters list the supported compilation paths and their
production risks.

Next: [submit your first task](getting_started.md).
