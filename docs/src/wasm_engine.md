# WebAssembly Execution Engine

Pyroxide includes a high-performance, sandboxed WebAssembly (WASM) execution engine powered by `wasmtime`. This engine allows you to run safe, compiled, low-latency code in background workers **without** having to rebuild or redeploy Pyroxide itself.

It provides a completely dynamic scripting alternative that runs at native execution speeds while remaining fully isolated from the host operating system.

---

## Architecture & Memory Protocol

Since WebAssembly runs in a strict sandbox, the guest module does not share memory addresses directly with Pyroxide. To pass data back and forth, Pyroxide implements a lightweight **Host-Guest Memory Protocol**:

1. **Host Allocation**: The host calls the guest's exported `alloc(size)` function to allocate a buffer of `size` bytes inside the WASM linear memory.
2. **Payload Transfer**: The host writes the input payload bytes (String or Bytes) directly into the guest memory at the returned offset pointer.
3. **Execution**: The host calls the target function (e.g. `run(ptr, len)`) returning a packed `u64` containing the output pointer and length:
   - `out_ptr` = high 32 bits
   - `out_len` = low 32 bits
4. **Result Retrieval**: The host reads the resulting bytes from the guest memory using the unpacked offset and length, then reconstructs the Python return type.
5. **Host Deallocation**: The host calls the guest's exported `dealloc(ptr, size)` function on both the input and output buffers to prevent memory leaks in the guest runtime.

---

## Writing a WASM Guest Module (Rust)

Here is a template for compiling a Rust module to `wasm32-unknown-unknown` that processes input text:

```rust
#![no_std]
#![no_main]

use core::panic::PanicInfo;

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}

// 64KB static buffer to simplify memory management
static mut BUFFER: [u8; 65536] = [0; 65536];

#[no_mangle]
pub extern "C" fn alloc(_size: u32) -> u32 {
    unsafe { BUFFER.as_mut_ptr() as u32 }
}

#[no_mangle]
pub extern "C" fn dealloc(_ptr: u32, _size: u32) {
    // No-op for static buffer, or implement dynamic heap dealloc
}

#[no_mangle]
pub unsafe extern "C" fn run(ptr: u32, len: u32) -> u64 {
    let slice = core::slice::from_raw_parts_mut(ptr as *mut u8, len as usize);
    for c in slice.iter_mut() {
        match *c {
            b'a'..=b'm' | b'A'..=b'M' => *c += 13,
            b'n'..=b'z' | b'N'..=b'Z' => *c -= 13,
            _ => {}
        }
    }
    // Return packed pointer (high 32 bits) and length (low 32 bits)
    ((ptr as u64) << 32) | (len as u64)
}
```

Compile the file using `rustc` directly:
```bash
rustc --target wasm32-unknown-unknown -O --crate-type=cdylib module.rs -o module.wasm
```

---

## Python Usage

### 1. Registering the Module

Load the compiled `.wasm` bytecode in Python and register it in Pyroxide's global module registry:

```python
from pyroxide import register_wasm

with open("module.wasm", "rb") as f:
    wasm_bytes = f.read()

# Register under a unique name
register_wasm("my_module", wasm_bytes)
```

### 2. Submitting WASM Tasks

Decorate standard functions using `@wasm_task` to offload work:

```python
from pyroxide import wasm_task

@wasm_task("my_module", "run")
def rot13_cipher(payload: str) -> str:
    """This function acts as a type stub. Execution is redirected to the WASM runner."""
    pass

# Run in background asynchronously (GIL-free)
handle = rot13_cipher("Hello World!")
print("Status:", handle.status)

# Await output
result = handle.result()
print("Decrypted:", result)  # "Uryyb Jbeyq!"
```

### 3. Object-Oriented WASM Proxies (v0.6.0)

If a WebAssembly module exports multiple functions (e.g. `compress`, `decompress`, `validate`), you can load the module as an object-oriented proxy using `load_wasm()`. 

This maps all exported WASM guest functions directly into Python methods:

```python
from pyroxide import register_wasm, load_wasm

# Register the module bytes
register_wasm("compression_mod", WASM_BYTES)

# Load the Object-Oriented Proxy!
compressor = load_wasm("compression_mod")

# Call exported WASM functions directly!
handle_zip = compressor.compress(b"raw data payload")
handle_unzip = compressor.decompress(handle_zip.result())

print("Final Output:", handle_unzip.result())
```

---

### IDE Autocomplete (Type Stub Generator)

Similar to dynamic libraries, you can generate standard Python PEP 484 type stub files (`.pyi`) for registered WASM modules, offering autocomplete for all exported functions:

```python
from pyroxide import generate_stubs

# Automatically parses the WASM module exports and writes a .pyi stub file
generate_stubs("compression_mod", library_type="wasm")
```

---

## Benefits of the WASM Engine

- **Safety & Isolation**: Code runs within the `wasmtime` sandbox. A crash or panic in guest code cannot crash the host Python runtime or the Pyroxide broker.
- **Dynamic Updates**: Register new modules and trigger task updates at runtime without restarting worker threads or redeploying code.
- **GIL-Free Speed**: Native execution runs concurrently across the worker pool without ever locking Python's GIL.

---

## WASM Resource Limits & Sandboxing

To prevent infinite loops and host process Out-Of-Memory (OOM) crashes, Pyroxide enforces resource limits on the WASM execution engine:

- **Epoch-Based Interruption (Timeout)**: Pyroxide runs a background ticking thread that advances the WASM engine epoch. Every WASM execution is assigned a deadline (default: 1000ms). If a guest module gets stuck in an infinite loop, it is interrupted and terminated with a trap error once the deadline is exceeded.
- **Linear Memory Limits**: Every WASM Store is configured with strict linear memory growth limits to prevent host process OOM (default: 100MB).

### Programmatic Configuration

Instead of relying on environment variables, you can configure these limits dynamically and safely in Python using `pyroxide.config`:

```python
import pyroxide

# --- 1. Global Setup ---
# Set global defaults (typically on application startup)
pyroxide.config.set_wasm_limits(
    memory_limit_bytes=50 * 1024 * 1024,  # 50 MB
    timeout_ms=500                       # 500 ms timeout
)
pyroxide.config.set_queue_timeout(timeout_ms=200)

# --- 2. Thread-Safe Scoped Overrides ---
# Restrict untrusted tenant code using context managers
with pyroxide.config.scoped(wasm_timeout_ms=100, wasm_memory_limit_bytes=10 * 1024 * 1024):
    # Tasks submitted within this block inherit these strict overrides
    handle = my_wasm_task(untrusted_input)
```

The scoped overrides are **thread-safe** (using `threading.local()`), ensuring multi-tenant web workers can safely submit tasks with custom limits without affecting other threads.

