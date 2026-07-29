# Native Library ABI

Native libraries are trusted host code. Loading one gives it the same process
privileges as Pyroxide. Process isolation provides crash containment, not an
operating-system security sandbox.

## Raw byte ABI

A raw native task exports:

```c
uint8_t *pyroxide_plugin_run(
    const uint8_t *input,
    size_t input_length,
    size_t *output_length
);

void pyroxide_plugin_free(uint8_t *output, size_t output_length);
```

The run function receives a borrowed input buffer that is valid only during
the call. A non-null output pointer transfers one allocation to Pyroxide.
Pyroxide copies the bounded output and calls the matching free function
exactly once.

Raw execution rejects empty payloads, null output pointers, missing free
functions, and output lengths above `PYROXIDE_MAX_NATIVE_OUTPUT_BYTES`.

## Primitive FFI ABI

The prepared FFI dispatcher supports zero through eight arguments and one
return value from:

- `i32`, `u32`
- `i64`, `u64`
- `isize`, `usize`
- `f32`, `f64`

Values use native byte order with standard sizes and no alignment padding.
`isize` and `usize` use the current process pointer width. The Python process
and library must therefore use the same architecture and ABI.

Payload length must equal the sum of argument widths. Return length must equal
the declared return width.

## Optional metadata

A library may export:

```c
const char *pyroxide_metadata(void);
```

The pointer may be null to signal an error. A non-null pointer must reference a
valid NUL-terminated UTF-8 string that remains alive after the call.

Entries use:

```text
symbol:arg1,arg2|return;symbol2:|return
```

Example:

```text
add:i32,i32|i32;clock:|u64
```

When metadata exists, the requested signature must match an entry exactly.
Invalid UTF-8, malformed presence fields, and more than eight arguments fail
closed.

## Library lifetime

The registry stores each `libloading::Library` in an `Arc`. Cached function
pointers are valid only while that library owner remains alive. Re-registering
or unregistering a library replaces or removes the registry owner after
in-flight clones release it.

Raw and prepared FFI pointers are never accepted directly from Python. They
are resolved from a registered live library and cached with their validated
signature.

## Compiler helpers

`compile_c`, `compile_rust`, and `compile_zig` are development conveniences.
They use a process-local lock plus a cross-process cache lock, write into a
temporary directory, and atomically publish the completed library.

Set `PYROXIDE_DISABLE_COMPILATION=1` in production when runtime compilation is
not allowed. Prebuild and review native libraries before deployment.
