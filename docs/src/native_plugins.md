# Native shared-library plugins

Pyroxide can call trusted C-ABI shared libraries on background threads without
holding the Python GIL. Supported file formats are `.so`, `.dylib`, and `.dll`.

Native code has unrestricted access to the host process. A bad pointer, incorrect
signature, panic across the ABI, buffer overflow, `abort()`, or segmentation fault
can corrupt or terminate Python. Rust panic handling cannot catch hardware faults
or undefined behavior.

Use `isolated=True` for process crash containment. It is still not an OS sandbox.

## Byte-buffer ABI

The default symbol receives bytes and returns an owned byte buffer:

```c
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

uint8_t *pyroxide_plugin_run(
    const uint8_t *input,
    size_t input_len,
    size_t *output_len
) {
    uint8_t *output = malloc(input_len);
    if (output == NULL && input_len != 0) return NULL;
    for (size_t i = 0; i < input_len; i++) output[i] = input[i];
    *output_len = input_len;
    return output;
}

void pyroxide_plugin_free(uint8_t *output, size_t output_len) {
    (void)output_len;
    free(output);
}
```

The plugin must:

- export the exact C ABI expected by the configured call;
- keep the returned allocation valid until the free callback;
- report its exact allocation length; and
- deallocate with the same allocator that created the buffer.

Pyroxide cannot validate that an arbitrary native pointer is valid before reading
it. It rejects a reported output larger than
`PYROXIDE_MAX_NATIVE_OUTPUT_BYTES` (64 MiB by default) before copying, but a
malformed pointer or deallocator can still crash the process. The ABI is a trust
boundary, not a memory-safety boundary.

## Register and call a precompiled library

```python
from pyroxide import register_dylib, dylib_task

register_dylib("codec", "/opt/myapp/libcodec.so")

@dylib_task("codec")
def transform(payload: bytes) -> bytes:
    pass

result = transform(b"data").result()
```

For crash containment:

```python
@dylib_task("codec", isolated=True)
def transform(payload: bytes) -> bytes:
    pass
```

## Numeric FFI signatures

`load_dylib()` can pack primitive arguments and unpack one primitive return value.
Supported types are `i32`, `i64`, `f32`, and `f64`.

```python
from pyroxide import load_dylib

math = load_dylib(
    "/opt/myapp/libmath.so",
    signatures={"scale": {"args": ["i32", "f64"], "ret": "i32"}},
)
print(math.scale(100, 1.5).result())
```

The declared signature must exactly match the exported function. A mismatch is
undefined behavior. Up to eight arguments are supported.

A library may export `pyroxide_metadata()` with entries such as
`scale:i32,f64|i32`; `load_dylib()` uses it only when no explicit signatures are
provided. Treat metadata from an untrusted library as untrusted native code too.

## Runtime compilation

`compile_rust`, `compile_c`, and `compile_zig` invoke local compiler processes,
cache the resulting library, and register it. Compilation is serialized across
threads and processes, has a timeout, and publishes cache files atomically.

```python
from pyroxide import compile_c

path = compile_c("codec", trusted_c_source)
```

Missing tools raise `CompilerNotFoundError`. Configure:

| Variable | Meaning | Default |
| --- | --- | --- |
| `PYROXIDE_CACHE_DIR` | Native compilation cache | `~/.pyroxide/cache` |
| `PYROXIDE_COMPILER_TIMEOUT_SEC` | Per compiler command timeout | `300` |
| `PYROXIDE_DISABLE_COMPILATION` | Reject runtime compilation when `1` or `true` | disabled |

Runtime compilation must never accept tenant or user-controlled source. Production
deployments should prefer reviewed, precompiled artifacts and set
`PYROXIDE_DISABLE_COMPILATION=1` when compilation is unnecessary.

## Unloading

`unregister_dylib(name)` removes future lookup registrations. Ensure no task is
using the library when unregistering it. Library unloading while native code is
executing is unsafe.
