# Native shared-library plugins

Use a native plugin when you already have a reviewed C-ABI library, or when a
hot path belongs in compiled code. Pyroxide calls `.so`, `.dylib`, and `.dll`
exports on background threads without holding the Python GIL.

## Choose a native workflow

1. Load a reviewed precompiled `.so`, `.dylib`, or `.dll`.
2. Use optional helpers to compile trusted C, Rust, or Zig source.

Both paths submit work through Pyroxide task handles and retain batching, async
results, and lifecycle controls. Both can opt into process crash containment
with `isolated=True`.

Native code has unrestricted access to its process. A bad pointer, wrong
signature, panic across the ABI, buffer overflow, `abort()`, or segmentation
fault can corrupt or terminate Python. Rust panic handling cannot catch hardware
faults or undefined behavior.

`isolated=True` can contain a crash to a worker process. It does not make native
code safe or turn the worker into a permission sandbox.

## Register and call a precompiled library

Precompiled artifacts are the preferred production path:

```python
from pyroxide import register_dylib, dylib_task

register_dylib("codec", "/opt/myapp/libcodec.so")

@dylib_task("codec")
def transform(payload: bytes) -> bytes:
    pass

result = transform(b"data").result()
```

For process crash containment:

```python
@dylib_task("codec", isolated=True)
def transform_safely(payload: bytes) -> bytes:
    pass
```

The decorator declares the interface. The registered library export performs
the work.

## Byte-buffer ABI

The default export receives bytes and returns an owned byte buffer:

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

- export the exact C ABI used by the call;
- keep the allocation valid until the free callback;
- report the exact allocation length; and
- free memory with the allocator that created it.

Pyroxide rejects a reported output larger than
`PYROXIDE_MAX_NATIVE_OUTPUT_BYTES`, 64 MiB by default, before copying it. It
cannot prove that an arbitrary pointer or deallocator is valid. This ABI is a
trust boundary, not a memory-safety boundary.

## Call several exports through a proxy

`load_dylib()` builds a proxy whose methods submit named symbols:

```python
from pyroxide import load_dylib

codec = load_dylib("codec")
compressed = codec.compress(b"data").result()
restored = codec.decompress(compressed).result()
```

Each unresolved method uses the byte-buffer ABI. Use explicit primitive
signatures when an export takes numbers:

```python
math = load_dylib(
    "/opt/myapp/libmath.so",
    signatures={
        "scale": {"args": ["i32", "f64"], "ret": "i32"},
    },
)
print(math.scale(100, 1.5).result())
```

Primitive names are `i32`, `i64`, `f32`, and `f64`, with up to eight
arguments. The dispatcher supports a defined subset of their possible
combinations; see [the exact signature matrix](universal_ffi.md#supported-signatures).
An unsupported combination raises `RuntimeError`. A declaration that is
accepted but does not match the real export is undefined behavior.

A library may expose metadata such as `scale:i32,f64|i32` from
`pyroxide_metadata()`. When no explicit signatures are supplied,
`load_dylib()` uses that metadata. Metadata does not make an untrusted library
safe.

## Generate proxy and stub files

Dynamic methods work at runtime but give an editor little type information.
Generate `.py` and `.pyi` helpers during development or packaging:

```bash
pyroxide build-stubs --scan --scan-dir . --out-dir generated
```

For a registered library, generation can also happen during loading:

```python
codec = load_dylib("codec", generate_stubs=True)
```

That writes files in the current directory. Prefer the CLI for production builds
so application startup does not modify the filesystem or trigger development
reloaders.

## Compile trusted source during development

Pyroxide can invoke installed Rust, C, or Zig toolchains, cache the resulting
library, and register it:

```python
from pyroxide import compile_c

path = compile_c("codec", trusted_c_source)
```

| Variable | Meaning | Default |
| --- | --- | --- |
| `PYROXIDE_CACHE_DIR` | Native compilation cache | `~/.pyroxide/cache` |
| `PYROXIDE_COMPILER_TIMEOUT_SEC` | Per-command timeout | `300` |
| `PYROXIDE_DISABLE_COMPILATION` | Reject runtime compilation when `1` or `true` | disabled |

Runtime compilation executes the compiler and its output with host permissions.
Never pass tenant- or user-controlled source. Ship reviewed precompiled
artifacts when possible, and set `PYROXIDE_DISABLE_COMPILATION=1` when the
application does not need compilation.

Missing toolchains raise `CompilerNotFoundError`. Compilation is serialized
across threads and processes, and cache publication is atomic.

## Unregistering

`unregister_dylib(name)` prevents future lookup through that registration.
Ensure that no task is using the library before unregistering it. Unloading a
library while native code is executing is unsafe.

For a smaller primitive-only interface to an existing system library, see
[Reusing existing libraries with FFI](universal_ffi.md). For deployment and
security settings, see [Production operations](operations.md).
