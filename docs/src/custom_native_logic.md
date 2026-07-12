# Custom Native Logic

While Pyroxide is primarily used to run standard Python functions concurrently, it also features a **GIL-Free Native Execution Engine** (`native=True`).

This chapter explains what native execution is, when to use it, and how to implement your own custom Rust processing logic inside the engine.

---

## What is Native Execution?

When you mark a task with `native=True`, the Python function body is ignored. Instead:
1. Python extracts the function's arguments (e.g., strings or bytes) and copies them into native Rust types (`String` or `Vec<u8>`). This input data is called the **payload**.
2. Pyroxide releases the Python Global Interpreter Lock (GIL).
3. The background thread processes the payload entirely in compiled Rust code without touching the Python interpreter.
4. Once completed, the thread briefly re-acquires the GIL to convert the result back to a Python object.

---

## How to Implement Custom Rust Logic

To add your own custom native processing operations:

### 1. Edit the Rust Worker (`src/worker.rs`)

Open `src/worker.rs` and locate the payload processing matching block. You can add a custom command matcher for your string payloads.

For example, to implement a native ROT13 cipher:

```rust
// In src/worker.rs inside the processed payload match block:
match payload {
    NativePayload::Str(s) => {
        if let Some(stripped) = s.strip_prefix("ROT13:") {
            let rot13_string = stripped.chars().map(|c| {
                match c {
                    'a'..='m' | 'A'..='M' => ((c as u8) + 13) as char,
                    'n'..='z' | 'N'..='Z' => ((c as u8) - 13) as char,
                    _ => c
                }
            }).collect::<String>();
            Ok(NativePayload::Str(rot13_string))
        } else if s == "TRIGGER_PANIC" {
            // ...
        }
    }
}
```

### 2. Compile and Install

Build and install your updated native extension into your local virtual environment:

```bash
maturin develop
```

### 3. Call it from Python

Now, you can trigger your custom Rust code directly from Python using the task decorator:

```python
from pyroxide import task

@task(native=True)
def cipher_text(payload: str) -> str:
    pass

# Sends the payload to the Rust worker. Runs completely GIL-free!
handle = cipher_text("ROT13:Hello World")
result = handle.result()
print(result) # Output: Uryyb Jbeyq
```
