# Dynamic Shared Libraries (dylib)

For use cases that require maximum performance and **unrestricted system access** (such as database connections, direct socket I/O, or local file access) but must avoid rebuilding Pyroxide itself, Pyroxide supports **Dynamic Shared Library** execution.

With this architecture:
- Workloads are executed completely **GIL-free** on background OS threads.
- You can compile source code **on-the-fly** at runtime, or load **pre-compiled** binaries directly.

---

## When to Use What

| Feature | `@task` | `@wasm_task` | `@dylib_task` |
| :--- | :--- | :--- | :--- |
| **Language** | Python | Any → WASM bytecode | Rust, C, Zig (C-ABI) |
| **GIL Status** | Held during callback | **GIL-Free** | **GIL-Free** |
| **System Access** | Full (Files, DB, Network) | Sandboxed (no OS access) | **Full (Files, DB, Network)** |
| **Safety** | High (exceptions caught) | **High** (sandbox traps caught) | Low (crash can segfault) |
| **Rebuild Required** | None | None | **None (auto-compiled / dynamic)** |
| **Best For** | General Python logic | Safe computation, untrusted code | High-perf DB/IO/algorithms |

---

## The ABI Contract

Any dynamic shared library loaded by Pyroxide (whether compiled on-the-fly or pre-compiled) must export exactly two C-compatible functions:

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

## 1. On-the-Fly Compilation

On-the-fly compilation compiles source code strings into shared libraries at runtime using your local toolchain, then registers them with the engine.

### Rust (`compile_dylib`)
Uses your local `cargo` toolchain under the hood to compile Rust source code. You can also specify Cargo dependencies.

```python
from pyroxide import compile_dylib, dylib_task

RUST_LOGGER = """
use std::fs::OpenOptions;
use std::io::Write;

#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_run(ptr: *const u8, len: usize, out_len: *mut usize) -> *mut u8 {
    let input = std::slice::from_raw_parts(ptr, len);
    let message = std::str::from_utf8(input).unwrap_or("invalid utf8");

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

@dylib_task("file_logger")
def log_event(message: str) -> str:
    pass

# Execute GIL-free
print(log_event("User login").result())  # "Logged: User login"
```

### C (`compile_c`)
Uses your local C compiler (`clang` or `gcc` via `CC` environment variable) to compile a C source string.

```python
from pyroxide import compile_c, dylib_task

C_SRC = """
#include <stdint.h>
#include <stdlib.h>

uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
    uint8_t* res = (uint8_t*)malloc(len);
    for (size_t i = 0; i < len; i++) {
        if (ptr[i] >= 'a' && ptr[i] <= 'z') {
            res[i] = ptr[i] - 32;
        } else {
            res[i] = ptr[i];
        }
    }
    *out_len = len;
    return res;
}

void pyroxide_plugin_free(uint8_t* ptr, size_t len) {
    free(ptr);
}
"""

compile_c("c_upper", C_SRC)

@dylib_task("c_upper")
def to_upper_c(payload: str) -> str:
    pass

print(to_upper_c("hello from c").result())  # "HELLO FROM C"
```

### Zig (`compile_zig`)
Uses your local `zig build-lib` toolchain to compile a Zig source string.

```python
from pyroxide import compile_zig, dylib_task

ZIG_SRC = """
const std = @import("std");

export fn pyroxide_plugin_run(ptr: [*]const u8, len: usize, out_len: *usize) [*]u8 {
    const allocator = std.heap.page_allocator;
    const output = allocator.alloc(u8, len) catch unreachable;
    @memcpy(output, ptr[0..len]);
    for (output) |*char| {
        if (char.* >= 'a' and char.* <= 'z') {
            char.* -= 32;
        }
    }
    out_len.* = len;
    return output.ptr;
}

export fn pyroxide_plugin_free(ptr: [*]u8, len: usize) void {
    const allocator = std.heap.page_allocator;
    allocator.free(ptr[0..len]);
}
"""

compile_zig("zig_upper", ZIG_SRC)

@dylib_task("zig_upper")
def to_upper_zig(payload: str) -> str:
    pass

print(to_upper_zig("hello from zig").result())  # "HELLO FROM ZIG"
```

### Compiler PATH Verification
To avoid cryptic system errors when dynamic compilation fails, Pyroxide performs early checks on the host environment's system path:
- `compile_dylib` verifies that `cargo` is available on the system `PATH`.
- `compile_c` verifies that the defined C compiler (e.g. `clang` or `gcc`, or custom via the `CC` environment variable) is present.
- `compile_zig` verifies that `zig` is present.

If a required compiler binary is missing, Pyroxide immediately raises a descriptive `RuntimeError` explaining what is missing.

---

## 2. Using Pre-Compiled Shared Libraries

If you already have a compiled shared library file (`.so` / `.dylib` / `.dll`), you can bypass the compilation phase entirely and load it directly using `register_dylib`.

### Supported Languages
Because `register_dylib` expects a standard C-ABI shared library, you can write the library in **any language** that supports compiling to a C-compatible shared library (including **Rust**, **C/C++**, **Zig**, **Go** via `-buildmode=c-shared`, **Nim**, **Fortran**, and others).

### Pre-Compiled Example (C Language)

Here is a C library example (`my_math.c`):

```c
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

// Required run symbol matching Pyroxide's expectations
uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
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
---

## 3. Object-Oriented Proxies & Custom Symbols (v0.6.0)

For libraries that expose multiple distinct operations (symbols), writing separate dummy Python stub functions for each symbol can be verbose. In Pyroxide v0.6.0, you can load a dynamic library as an object-oriented proxy using `load_dylib()`.

This allows calling **any** C-compatible exported symbol directly as a method on the proxy object.

### Example: Multi-Function C Library
Suppose you have a C library that exports `hash_sha256` and `encrypt_aes`:

```c
#include <stdint.h>
#include <stdlib.h>

uint8_t* hash_sha256(const uint8_t* ptr, size_t len, size_t* out_len) {
    // hashing logic...
}

uint8_t* encrypt_aes(const uint8_t* ptr, size_t len, size_t* out_len) {
    // encryption logic...
}

void pyroxide_plugin_free(uint8_t* ptr, size_t len) {
    free(ptr);
}
```

You can compile, load, and call these symbols directly in Python:

```python
from pyroxide import compile_c, load_dylib

# Compile and register
compile_c("crypto_lib", CRYPTO_C_SRC)

# Load the Object-Oriented Proxy!
crypto = load_dylib("crypto_lib", isolated=True)

# Call custom symbols directly!
# Symbol resolution is cached automatically on first invocation.
handle_hash = crypto.hash_sha256(b"message payload")
handle_encrypt = crypto.encrypt_aes(b"sensitive data")

print(handle_hash.result())
print(handle_encrypt.result())
```

### Static Type Dispatching (Mini-FFI)

By default, Pyroxide native symbols must accept a byte pointer and return a byte pointer. However, you can use the `signatures` parameter to call arbitrary C-ABI functions using numeric arguments directly (e.g. `i32`, `i64`, `f32`, `f64`). 

Pyroxide will automatically pack arguments into bytes on the Python side using `struct.pack` and unpack them in Rust.

#### Example:
Suppose your dynamic library exports a numeric function:
```c
int32_t calculate_hash(int32_t a, double factor) {
    return (int32_t)(a * factor);
}
```

You can call it in Python with native integers and floats:
```python
math_lib = load_dylib(
    "math_lib",
    signatures={
        "calculate_hash": {"args": ["i32", "f64"], "ret": "i32"}
    }
)

# Arguments are packed, dispatched, and the returned i32 is unpacked automatically!
handle = math_lib.calculate_hash(100, 1.5)
print(handle.result()) # 150
```

Supported types include `i32`, `i64`, `f32`, and `f64`. Under the hood, Pyroxide implements a compiled macro-based trait dispatcher supporting up to **8 arguments** with arbitrary signatures, executing compiled symbols at native CPU register speeds without runtime FFI engine overhead.

#### Zero-Configuration Signature Discovery (`pyroxide_metadata`)
Instead of manually declaring signatures in Python, you can export a special metadata symbol named `pyroxide_metadata` from your C/Rust library. Pyroxide will auto-discover the signatures at runtime:

```c
const char* pyroxide_metadata() {
    // Format: "method_name:args|return_type;..."
    return "calculate_hash:i32,f64|i32;my_other_func:f64|f64";
}
```

When loading the library, you do not need to provide the `signatures` dictionary:
```python
# Automatically parses pyroxide_metadata symbol and sets up types!
math_lib = load_dylib("math_lib")
```

---

### IDE Autocomplete (Type Stub Generator)

Since dynamic proxy objects use runtime dynamic lookup (`__getattr__`), editors like VS Code won't show autocompletion for dynamic methods. 

To solve this, you can pass `generate_stubs=True` directly when loading a library, or use the `generate_stubs` helper:

```python
from pyroxide import load_dylib, generate_stubs

# Option A: Generate stubs inline during loading (ideal for development)
math_lib = load_dylib("math_lib", generate_stubs=True)

# Option B: Run the generator script manually
generate_stubs("math_lib", library_type="dylib")
```

Once the stub file is generated in your project (e.g. `math_lib_proxy.pyi`), VS Code or PyCharm will instantly show full autocompletion and hover documentation for all your native dynamic methods!

---

## Security Warning

> [!CAUTION]
> Dynamically loaded shared libraries run directly inside CPython's process memory.
> An unhandled segfault, null pointer dereference, or buffer overflow **will crash the entire Python process**.
> Only load trusted code via compilation helpers or `register_dylib()`.
