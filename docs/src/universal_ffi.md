# Universal FFI: Reusing Precompiled Libraries

In addition to compiling and loading custom code on-the-fly, Pyroxide can load and run **any existing, precompiled shared library** (`.so` on Linux, `.dylib` on macOS, `.dll` on Windows) directly.

This is called **Universal FFI** mode. It allows you to run functions from system libraries (like `libssl`, `libcrypto`, or standard math libraries) GIL-free on background threads with zero boilerplate.

---

## 1. Quick Example: Using the System Math Library

Here is how you can load the standard C math library and execute its trigonometric functions concurrently:

```python
import sys
import asyncio
from pyroxide import load_dylib

# 1. Resolve the path of the system math library
libm_name = "libm.dylib" if sys.platform == "darwin" else "libm.so.6"

# 2. Load the library and define signatures
libm = load_dylib(libm_name, signatures={
    "cos": {"args": ["f64"], "ret": "f64"},
    "sin": {"args": ["f64"], "ret": "f64"}
})

# 3. Call functions GIL-free on the background thread pool
async def main():
    h1 = libm.cos(3.1415926535)
    h2 = libm.sin(1.5707963267)
    
    # Await results concurrently via the Native Event Loop Waker
    cos_res, sin_res = await asyncio.gather(
        h1.result_async(),
        h2.result_async()
    )
    print(f"cos(pi) = {cos_res}") # -1.0
    print(f"sin(pi/2) = {sin_res}") # 1.0

asyncio.run(main())
```

---

## 2. Memory Management & Safety Rules

When loading external libraries, memory management is split between stack-allocated primitives and heap-allocated pointer returns:

### Stack-Returned Primitives (No Free Required)
If your FFI method signature returns primitive numeric values (e.g. `i32`, `i64`, `f32`, `f64`, `bool`):
*   **No deallocator is needed.**
*   The values are copied directly over the FFI boundary.
*   You can load these functions from **any** standard C library without writing wrapper files.

### Heap-Returned Pointer Types (Free Function Required)
If your FFI method signature returns raw pointers (represented as `"string"` or `"bytes"`):
*   **A deallocator is required.**
*   Under the hood, the return value is a heap pointer (`*mut u8` or `*mut c_char`). Once Python copies the data, the heap buffer must be freed to prevent memory leaks.
*   By default, Pyroxide looks for a symbol named **`pyroxide_plugin_free`**.
*   If the library does not export `pyroxide_plugin_free`, Pyroxide will raise a `RuntimeError` at task submission time unless a custom deallocator name is specified.
