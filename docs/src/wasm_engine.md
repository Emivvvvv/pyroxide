# WebAssembly execution

Use WebAssembly when an application needs to run a portable guest module
without giving it file, socket, or environment imports through Pyroxide.
Wasmtime applies a memory limit and an epoch-time deadline to every invocation.

This is an application-level isolation boundary, not an absolute security
promise. Validate inputs, keep Pyroxide and Wasmtime updated, run the host with
least privilege, and test hostile modules against your own threat model.

## Register and call a module

```python
from pyroxide import register_wasm, wasm_task

with open("codec.wasm", "rb") as stream:
    register_wasm("codec", stream.read())

@wasm_task("codec", "run")
def transform(payload: bytes) -> bytes:
    pass

print(transform(b"data").result())
```

The decorated function is an interface declaration; the guest export performs
the work. Payloads may be `bytes` or UTF-8 `str`, and the result follows the
input representation.

Use a proxy when the module has several exports:

```python
from pyroxide import load_wasm

codec = load_wasm("codec")
compressed = codec.compress(b"data").result()
restored = codec.decompress(compressed).result()
```

`isolated=True` adds a worker-process boundary. It usually adds overhead without
changing the imports available to the WASM guest.

## Guest ABI

A callable core WebAssembly module exports:

```text
memory
alloc(size: i32) -> i32
dealloc(ptr: i32, size: i32)
run(ptr: i32, size: i32) -> i64
```

The result packs the output pointer in the high 32 bits and output length in the
low 32 bits. A different exported function name may replace `run`.

For each call, Pyroxide:

1. checks the input against the configured limit;
2. allocates guest memory and copies the input;
3. invokes the export with an epoch deadline;
4. validates the returned pointer, length, range, and configured limit;
5. copies the output to Python; and
6. calls the guest deallocator.

Inputs and outputs are copied across the boundary. Pyroxide 1.0 does not expose
WASI, the Component Model, custom host imports, shared-memory threads, or
arbitrary typed calls.

## Limits

| Setting | Default |
| --- | --- |
| Memory per invocation | 100 MiB |
| Execution deadline | 1000 ms |
| Epoch tick | 10 ms |

Set process-wide defaults:

```python
import pyroxide

pyroxide.set_wasm_limits(
    memory_limit_bytes=50 * 1024 * 1024,
    timeout_ms=500,
)
```

Use a scoped override for tasks submitted in one thread:

```python
with pyroxide.scoped(
    wasm_memory_limit_bytes=10 * 1024 * 1024,
    wasm_timeout_ms=100,
):
    handle = transform(b"tenant input")
```

Programmatic global settings take precedence over environment settings. Memory
must be between 1 byte and `2**31 - 1`; timeouts and tick intervals must be
positive.

An epoch deadline is not a real-time guarantee. Wasmtime observes it at an
engine epoch check, so scheduler delay and tick granularity add latency.

## Traps and debugging

Trap messages include WebAssembly function names when the module provides them.
Set `WASMTIME_BACKTRACE_DETAILS=1` before the first module registration to add
source locations from guest DWARF data. Debug data increases module startup time
and memory use.

WASM execution deadlines are separate from `TaskHandle.cancel()`. A running
in-process guest cannot be user-cancelled safely, but it traps after its engine
deadline.

## Proxies and type stubs

Dynamic proxies are convenient at runtime. For editor completion and deployments
that should not write files during startup, generate `.py` and `.pyi` helpers
ahead of time:

```bash
pyroxide build-stubs --scan --scan-dir . --out-dir generated
```

You can also request generation while loading a registered module:

```python
codec = load_wasm("codec", generate_stubs=True)
```

That writes helpers in the current directory, so static generation is normally
the better production workflow.

## Runtime compilation

`compile_wat_wasm`, `compile_c_wasm`, `compile_rust_wasm`, and
`compile_zig_wasm` are development conveniences. The C, Rust, and Zig helpers
invoke local toolchains before Wasmtime runs the compiled guest.

Source compilation itself is not sandboxed: compilers, plugins, and build
scripts run with host permissions. Prefer reviewed `.wasm` artifacts in
production and set `PYROXIDE_DISABLE_COMPILATION=1` when runtime compilation is
not required.

See [Choosing an execution mode](execution_modes.md) for boundary trade-offs and
[Production operations](operations.md) for global configuration.
