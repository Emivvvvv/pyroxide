# Reusing existing native libraries

Use `load_dylib()` when an application needs a small, reviewed primitive C-ABI
surface from an existing shared library. This avoids writing a wrapper for a
handful of numeric calls.

It is not a general C header parser and does not make an unsafe API safe.

```python
import sys
from pyroxide import load_dylib

library = "libm.dylib" if sys.platform == "darwin" else "libm.so.6"
math = load_dylib(
    library,
    signatures={
        "cos": {"args": ["f64"], "ret": "f64"},
        "sin": {"args": ["f64"], "ret": "f64"},
    },
)

print(math.cos(3.1415926535).result())
```

## Supported signatures

Primitive names are `i32`, `i64`, `f32`, and `f64`. Return values must use one
of those four types; the primitive dispatcher does not support `void`.

The dispatcher accepts these argument shapes:

| Argument count | Supported argument shapes | Supported returns |
| --- | --- | --- |
| 1 | Any one supported primitive | `i32`, `i64`, `f32`, or `f64` |
| 2 | `i32,i32`; `i32,f64`; `f64,i32`; `f64,f64` | Any supported primitive |
| 2 | `i64,i64` | `i32`, `i64`, or `f64` |
| 2 | `f32,f32` | `f32` or `f64` |
| 3 | `i32,i32,i32`; `f64,f64,f64` | `i32`, `i64`, or `f64` |
| 3 | `i32,i32,f64`; `f64,f64,i32` | `i32` or `f64` |
| 3 | `i64,i64,i64` | `i64` |
| 4 | Four `i32` values or four `f64` values | `i32`, `i64`, or `f64` |
| 4 | `i32,i32,f64,f64` | `i32` or `f64` |
| 4 | Four `i64` values | `i64` |
| 5-8 | All `i32` or all `f64` | `i32`, `i64`, or `f64` |
| 5-8 | All `i64` | `i64` |

Other combinations raise `RuntimeError("Unsupported FFI signature mapping")`.
The declared signature must still match the real export exactly.

## Safety rules

- Verify the library path and binary provenance.
- Declare the exact exported ABI. Calling-convention or type mismatches are
  undefined behavior.
- Do not use this primitive signature interface for arbitrary pointers, structs,
  variadic functions, callbacks, or ownership-bearing values.
- The default byte-buffer ABI requires a matching deallocator; see
  [Native plugins](native_plugins.md).
- Use `isolated=True` for crash containment, understanding that it is not a
  permission sandbox.

Platform library names differ and may not be present in minimal containers. Pin
or package the libraries your application requires instead of depending on
ambient system versions.

See [Native shared-library plugins](native_plugins.md) for byte buffers,
generated proxies, runtime compilation, and the full safety contract.
