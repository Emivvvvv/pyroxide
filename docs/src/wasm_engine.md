# WebAssembly execution

Pyroxide runs registered WebAssembly modules with Wasmtime on background threads.
The linker supplies no host imports, so a module cannot access files, sockets, or
environment variables through Pyroxide. Memory and epoch deadlines bound each
invocation.

This is a useful application-level sandbox, but production users should still
validate inputs, keep Wasmtime and Pyroxide updated, use least-privilege host
processes, and test hostile modules against their own threat model.

## Guest ABI

A callable module exports:

```text
memory
alloc(size: i32) -> i32
dealloc(ptr: i32, size: i32)
run(ptr: i32, size: i32) -> i64
```

The result packs the output pointer in the high 32 bits and output length in the
low 32 bits. A custom function name may replace `run`.

For each call, Pyroxide:

1. validates the input size against the configured limit;
2. allocates and writes the input in guest memory;
3. invokes the export with an epoch deadline;
4. validates that the returned pointer and length are non-negative, in bounds,
   non-overflowing, and within the configured limit;
5. copies the output to the host; and
6. calls the guest deallocator.

Inputs and outputs are copied across the sandbox boundary. Payloads are `bytes` or
UTF-8 `str`; the result follows the input representation.

Pyroxide 1.0 supports core WebAssembly modules using this ABI. It does not expose
WASI, the Component Model, custom host imports, shared-memory threads, or
arbitrary typed function calls.

## Trap diagnostics

Trap messages include WebAssembly function names when the module provides them.
To include source locations from guest DWARF data, set
`WASMTIME_BACKTRACE_DETAILS=1` before the first WebAssembly module is registered.
Parsing and retaining debug data adds module startup and memory overhead.

## Register and execute

```python
from pyroxide import register_wasm, wasm_task

with open("codec.wasm", "rb") as stream:
    register_wasm("codec", stream.read())

@wasm_task("codec", "run")
def transform(payload: bytes) -> bytes:
    pass

print(transform(b"data").result())
```

For multiple exports:

```python
from pyroxide import load_wasm

codec = load_wasm("codec")
result = codec.compress(b"data").result()
```

`isolated=True` adds process crash containment, but usually adds overhead without
strengthening Wasmtime's guest permissions.

## Limits

Defaults apply at process startup:

| Setting | Default |
| --- | --- |
| Memory per invocation | 100 MiB |
| Execution deadline | 1000 ms |
| Epoch tick | 10 ms |

```python
import pyroxide

pyroxide.set_wasm_limits(memory_limit_bytes=50 * 1024 * 1024, timeout_ms=500)

with pyroxide.scoped(
    wasm_memory_limit_bytes=10 * 1024 * 1024,
    wasm_timeout_ms=100,
):
    handle = transform(b"tenant input")
```

Programmatic global settings take precedence over environment settings.
Thread-local scoped values affect tasks submitted inside that scope. Memory must
be between 1 byte and `2**31 - 1`; timeouts and tick intervals must be positive.

An epoch deadline is not a real-time guarantee. A trap is observed on an engine
epoch check, so scheduling and tick granularity add latency.

## Runtime compilation

`compile_wat_wasm`, `compile_c_wasm`, `compile_rust_wasm`, and
`compile_zig_wasm` are development conveniences. C, Rust, and Zig helpers invoke
local toolchains and execute their output through Wasmtime. Source compilation is
not a sandbox: compiler plugins, build scripts, and toolchains run with host
permissions.

Prefer reviewed, precompiled `.wasm` artifacts in production. Set
`PYROXIDE_DISABLE_COMPILATION=1` when runtime compilation is unnecessary.
