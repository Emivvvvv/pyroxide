# Dynamic Shared Libraries (dylib)

For use cases that require maximum performance and **unrestricted system access** (such as database connections, direct socket I/O, or local file access) but must avoid rebuilding Pyroxide itself, Pyroxide supports **Dynamic Shared Library** execution via `compile_dylib()` and `@dylib_task`.

With this architecture:
- Users write Rust source code as a Python string.
- Pyroxide compiles it **on-the-fly at runtime** using the developer's local `cargo` toolchain.
- The compiled shared library (`.so` / `.dylib` / `.dll`) is dynamically loaded into the process and executed completely **GIL-free**.

---

## When to Use What

| Feature | `@task` | `@wasm_task` | `@dylib_task` |
| :--- | :--- | :--- | :--- |
| **Language** | Python | Any → WASM bytecode | Rust (compiled on-the-fly) |
| **GIL Status** | Held during callback | **GIL-Free** | **GIL-Free** |
| **System Access** | Full (Files, DB, Network) | Sandboxed (no OS access) | **Full (Files, DB, Network)** |
| **Safety** | High (exceptions caught) | **High** (sandbox traps caught) | Low (crash can segfault) |
| **Rebuild Required** | None | None | **None (auto-compiled)** |
| **Best For** | General Python logic | Safe computation, untrusted code | High-perf DB/IO/algorithms |

---

## The ABI Contract

Dynamic libraries must export exactly two C-compatible functions:

```rust
/// Executes the plugin logic. Receives input bytes, returns output bytes.
/// The output buffer MUST be allocated by the plugin's own allocator.
#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_run(
    ptr: *const u8,    // Pointer to input bytes
    len: usize,        // Length of input bytes
    out_len: *mut usize // Write output length here
) -> *mut u8;          // Return pointer to output bytes

/// Deallocates the output buffer returned by pyroxide_plugin_run.
/// Required because host and plugin may use different allocators.
#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_free(
    ptr: *mut u8,      // Pointer to free
    len: usize         // Length of allocation
);
```

> **Why two functions?** Memory allocated inside a dynamic library cannot be safely freed by the host process (they may use different allocators). The `_free` function ensures deallocation happens on the correct allocator.

---

## Python API

### `compile_dylib(name, source_code, dependencies=None)`

Compiles Rust source code into a shared library and registers it with the engine.

| Parameter | Type | Description |
| :--- | :--- | :--- |
| `name` | `str` | Unique identifier for this dylib |
| `source_code` | `str` | Raw Rust source code string |
| `dependencies` | `dict` | Optional Cargo dependencies, e.g. `{"serde": "1.0"}` |

### `@dylib_task(dylib_name)`

Decorator that routes payloads to the named dylib for GIL-free execution.

---

## Complete Example: File-Writing Native Logger

```python
from pyroxide import compile_dylib, dylib_task

RUST_LOGGER = """
use std::fs::OpenOptions;
use std::io::Write;

#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_run(ptr: *const u8, len: usize, out_len: *mut usize) -> *mut u8 {
    let input = std::slice::from_raw_parts(ptr, len);
    let message = std::str::from_utf8(input).unwrap_or("invalid utf8");

    // Dynamic libraries have full filesystem access
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open("app.log") {
        let _ = writeln!(file, "[Log] {}", message);
    }

    let response = format!("Logged: {}", message).into_bytes();
    *out_len = response.len();
    let boxed = response.into_boxed_slice();
    Box::into_raw(boxed) as *mut u8
}

#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_free(ptr: *mut u8, len: usize) {
    let _ = Box::from_raw(std::slice::from_raw_parts_mut(ptr, len));
}
"""

# Compile and register. Optional Cargo dependencies can be provided.
compile_dylib("file_logger", RUST_LOGGER)

# Decorate a stub to submit tasks to the dylib
@dylib_task("file_logger")
def log_event(message: str) -> str:
    pass

# Execute GIL-free with full OS access!
handle = log_event("User login at 12:00")
print(handle.result())  # "Logged: User login at 12:00"
```

---

## Using Pre-Compiled Shared Libraries

If you already have a compiled shared library file (`.so` / `.dylib` / `.dll`), you can bypass the compilation phase entirely and load it directly using `register_dylib`.

### Supported Languages
Because `register_dylib` expects a standard C-ABI shared library, you can write the library in **any language** that supports compiling to a C shared library:
- **Rust**
- **C / C++**
- **Zig**
- **Go** (via `-buildmode=c-shared`)

### Pre-Compiled Example (C Language)

Here is a C library example (`my_math.c`):

```c
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

// Required run symbol matching Pyroxide's expectations
uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
    // Basic echo with C-ABI
    uint8_t* result = (uint8_t*)malloc(len);
    memcpy(result, ptr, len);
    *out_len = len;
    return result;
}

// Required free symbol
void pyroxide_plugin_free(uint8_t* ptr, size_t len) {
    free(ptr);
}
```

Compile it to a shared library:
```bash
gcc -shared -o libmy_math.so -fPIC my_math.c
```

Load and execute it in Python:
```python
from pyroxide import register_dylib, dylib_task

# Load the pre-compiled C library directly
register_dylib("c_math", "./libmy_math.so")

@dylib_task("c_math")
def process_data(payload: bytes) -> bytes:
    pass

handle = process_data(b"hello C-ABI")
print(handle.result())  # b"hello C-ABI"
```

---

## Security Warning

> [!CAUTION]
> Dynamically loaded shared libraries run directly inside CPython's process memory.
> An unhandled segfault, null pointer dereference, or buffer overflow **will crash the entire Python process**.
> Only load trusted code via `compile_dylib()` or `register_dylib()`.
