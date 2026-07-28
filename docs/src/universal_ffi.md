# Reusing existing native libraries

Use `load_dylib()` when an application needs a small, reviewed primitive C-ABI
surface from an existing shared library. This avoids writing a wrapper for a
small number of numeric calls.

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

## Supported primitive types

The primitive dispatcher supports:

| Pyroxide type | Typical native type                                                    | Python value |
| ------------- | ---------------------------------------------------------------------- | ------------ |
| `i32`         | Rust `i32` or C `int32_t`                                              | `int`        |
| `u32`         | Rust `u32` or C `uint32_t`                                             | `int`        |
| `i64`         | Rust `i64` or C `int64_t`                                              | `int`        |
| `u64`         | Rust `u64` or C `uint64_t`                                             | `int`        |
| `isize`       | Rust `isize` or a matching signed pointer-width integer                | `int`        |
| `usize`       | Rust `usize`, C `size_t`, or a matching unsigned pointer-width integer | `int`        |
| `f32`         | Rust `f32` or C `float`                                                | `float`      |
| `f64`         | Rust `f64` or C `double`                                               | `float`      |

`usize` and `isize` follow the pointer width of the running Python process and
loaded library. They are 64-bit in a 64-bit process and 32-bit in a 32-bit
process.

Do not use `usize` or `isize` as portable aliases for C `unsigned long` or
`long`. The width of those C types varies between platforms.

Return values must use one of the supported primitive types. The primitive
dispatcher does not support `void`.

## Supported signature shapes

In the following table:

* `T`, `T1`, and `T2` mean any supported primitive type.
* `R` means any supported primitive return type.
* A homogeneous shape repeats the same argument type in every position.

| Argument count | Supported argument shapes    | Supported returns       |
| -------------: | ---------------------------- | ----------------------- |
|              0 | No arguments                 | Any supported primitive |
|              1 | `T`                          | Any supported primitive |
|              2 | Every `T1,T2` combination    | Any supported primitive |
|              3 | Homogeneous `T,T,T`          | Any supported primitive |
|              3 | `i32,i32,f64`; `f64,f64,i32` | Any supported primitive |
|              4 | Homogeneous `T,T,T,T`        | Any supported primitive |
|              4 | `i32,i32,f64,f64`            | Any supported primitive |
|            5–8 | Homogeneous arguments only   | Any supported primitive |

Examples of supported signatures include:

```text
|u64
u32|u32
u32,f64|usize
u64,u64|u64
usize,usize|usize
f32,f32,f32|f64
i32,i32,f64|u32
u64,u64,u64,u64|u64
i32,i32,i32,i32,i32,i32,i32,i32|i64
```

The zero-argument metadata form places no text before `|`:

```text
current_counter:|u64
```

Support for a primitive type does not imply support for every high-arity
combination. Other combinations raise an error similar to:

```text
RuntimeError(
    "Unsupported FFI signature mapping: "
    "(u32, f64, usize) -> u64"
)
```

The declared signature must match the real export exactly.

## Unsigned example

```python
from pyroxide import load_dylib

counter = load_dylib(
    "./libcounter.so",
    signatures={
        "read_counter": {
            "args": [],
            "ret": "u64",
        },
        "add_to_counter": {
            "args": ["u64"],
            "ret": "u64",
        },
        "combine_flags": {
            "args": ["u32", "u32"],
            "ret": "u32",
        },
    },
)

print(counter.read_counter().result())
print(counter.add_to_counter(4_000_000_000).result())
```

Python integers are range-checked against the declared primitive.

For example, a `u32` argument accepts:

```text
0 through 4294967295
```

Negative values and values outside the declared width are rejected rather than
wrapped.

## Safety rules

* Verify the library path and binary provenance.
* Declare the exact exported C ABI.
* Signedness is part of the signature. Do not declare `uint32_t` as `i32`.
* Calling-convention, width, signedness, or return-type mismatches are undefined behavior.
* Do not assume that C `long` and `unsigned long` have the same width on every platform.
* The running Python process and loaded library must use compatible architectures and ABIs.
* Do not use this primitive interface for pointers, strings, structs, arrays, callbacks, output parameters, or ownership-bearing values.
* The default byte-buffer ABI requires a matching deallocator; see [Native plugins](native_plugins.md).
* Use `isolated=True` for crash containment, understanding that it is not a permission sandbox.

Platform library names differ and may not be present in minimal containers.
Pin or package the libraries an application requires instead of depending on
ambient system versions.

See [Native shared-library plugins](native_plugins.md) for byte buffers,
generated proxies, runtime compilation, and the complete safety contract.
