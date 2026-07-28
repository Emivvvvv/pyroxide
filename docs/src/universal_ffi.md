# Reusing existing native libraries

`load_dylib()` can call primitive C-ABI functions from an existing shared library.
This is useful for a small, reviewed FFI surface. It is not a general C header
parser and does not make unsafe APIs safe.

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

Supported primitive argument and return types are `i32`, `i64`, `f32`, and
`f64`, with at most eight arguments. A `void` return can be represented by a type
other than the supported return names and produces `None`.

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
