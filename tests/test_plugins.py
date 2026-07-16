import pytest
import os
from pyroxide import compile_rust, dylib_task

RUST_PLUGIN_SRC = """
use std::fs;

#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_run(ptr: *const u8, len: usize, out_len: *mut usize) -> *mut u8 {
    let input = std::slice::from_raw_parts(ptr, len);
    let input_str = std::str::from_utf8(input).unwrap_or("unknown");
    
    // Write marker file to verify full OS / filesystem access
    let _ = fs::write("temp_dylib_marker.txt", format!("payload:{}", input_str));
    
    let result = format!("DYLIB: {}", input_str).into_bytes();
    *out_len = result.len();
    let boxed = result.into_boxed_slice();
    Box::into_raw(boxed) as *mut u8
}

#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_free(ptr: *mut u8, len: usize) {
    let _ = Box::from_raw(std::slice::from_raw_parts_mut(ptr, len));
}
"""


def test_dylib_lifecycle():
    """Verifies end-to-end dylib compilation, registration, execution, and OS access."""
    marker_path = "temp_dylib_marker.txt"
    if os.path.exists(marker_path):
        os.remove(marker_path)

    compile_rust("dyn_greeter", RUST_PLUGIN_SRC)

    @dylib_task("dyn_greeter")
    def greet(payload: str) -> str:
        pass

    handle = greet("Developer")
    assert handle.status in ("Pending", "Running", "Completed")
    res = handle.result()
    assert res == "DYLIB: Developer"

    # Verify that the dylib executed with full OS file-writing access
    assert os.path.exists(marker_path)
    with open(marker_path, "r") as f:
        content = f.read()
    assert content == "payload:Developer"
    os.remove(marker_path)


def test_dylib_bytes():
    """Verifies that dylib tasks correctly handle raw byte payloads."""
    compile_rust("dyn_bytes", RUST_PLUGIN_SRC)

    @dylib_task("dyn_bytes")
    def process_bytes(payload: bytes) -> bytes:
        pass

    handle = process_bytes(b"raw-data-bytes")
    res = handle.result()
    assert res == b"DYLIB: raw-data-bytes"


def test_dylib_compilation_failure():
    """Verifies that malformed Rust source code raises a clear RuntimeError."""
    bad_src = "this is not rust code"
    with pytest.raises(RuntimeError) as exc_info:
        compile_rust("dyn_bad", bad_src)
    assert "Failed to compile dylib" in str(exc_info.value)


def test_dylib_oop_proxy():
    """Verifies that load_dylib loads an OOP proxy resolving custom symbols dynamically."""
    from pyroxide import load_dylib

    RUST_OOP_SRC = """
    #[no_mangle]
    pub unsafe extern "C" fn custom_add(ptr: *const u8, len: usize, out_len: *mut usize) -> *mut u8 {
        let input = std::slice::from_raw_parts(ptr, len);
        let s = std::str::from_utf8(input).unwrap_or("");
        let result = format!("ADD: {}", s).into_bytes();
        *out_len = result.len();
        let boxed = result.into_boxed_slice();
        Box::into_raw(boxed) as *mut u8
    }

    #[no_mangle]
    pub unsafe extern "C" fn custom_mul(ptr: *const u8, len: usize, out_len: *mut usize) -> *mut u8 {
        let input = std::slice::from_raw_parts(ptr, len);
        let s = std::str::from_utf8(input).unwrap_or("");
        let result = format!("MUL: {}", s).into_bytes();
        *out_len = result.len();
        let boxed = result.into_boxed_slice();
        Box::into_raw(boxed) as *mut u8
    }

    #[no_mangle]
    pub unsafe extern "C" fn pyroxide_plugin_free(ptr: *mut u8, len: usize) {
        let _ = Box::from_raw(std::slice::from_raw_parts_mut(ptr, len));
    }
    """

    compile_rust("dyn_oop", RUST_OOP_SRC)
    proxy = load_dylib("dyn_oop")

    # Call custom symbols
    handle_add = proxy.custom_add("hello")
    handle_mul = proxy.custom_mul("world")

    assert handle_add.result() == "ADD: hello"
    assert handle_mul.result() == "MUL: world"


def test_dylib_mini_ffi():
    """Verifies that load_dylib works with signatures dictionary for FFI calls."""
    from pyroxide import load_dylib, compile_rust

    RUST_FFI_SRC = """
    #[no_mangle]
    pub extern "C" fn ffi_add(a: i32, b: i32) -> i32 {
        a + b
    }

    #[no_mangle]
    pub extern "C" fn ffi_double(x: f64) -> f64 {
        x * 2.0
    }

    #[no_mangle]
    pub unsafe extern "C" fn pyroxide_plugin_free(ptr: *mut u8, len: usize) {
        // Dummy
    }
    """

    compile_rust("dyn_ffi", RUST_FFI_SRC)

    # Load with signatures
    proxy = load_dylib(
        "dyn_ffi",
        signatures={
            "ffi_add": {"args": ["i32", "i32"], "ret": "i32"},
            "ffi_double": {"args": ["f64"], "ret": "f64"},
        },
    )

    # Call custom FFI symbols directly!
    handle_add = proxy.ffi_add(40, 2)
    handle_double = proxy.ffi_double(3.14)

    assert handle_add.result() == 42
    assert handle_double.result() == 6.28


def test_dylib_ffi_large_signature():
    """Verifies FFI dispatcher with a large 8-argument signature."""
    from pyroxide import load_dylib, compile_rust

    RUST_LARGE_SRC = """
    #[no_mangle]
    pub extern "C" fn ffi_sum_8(
        a: i32, b: i32, c: i32, d: i32,
        e: i32, f: i32, g: i32, h: i32
    ) -> i32 {
        a + b + c + d + e + f + g + h
    }

    #[no_mangle]
    pub unsafe extern "C" fn pyroxide_plugin_free(ptr: *mut u8, len: usize) {
        // Dummy
    }
    """

    compile_rust("dyn_large_ffi", RUST_LARGE_SRC)

    proxy = load_dylib(
        "dyn_large_ffi",
        signatures={
            "ffi_sum_8": {
                "args": ["i32", "i32", "i32", "i32", "i32", "i32", "i32", "i32"],
                "ret": "i32",
            }
        },
    )

    handle = proxy.ffi_sum_8(1, 2, 3, 4, 5, 6, 7, 8)
    assert handle.result() == 36


def test_dylib_no_free_success():
    """Verifies that FFI signature tasks can execute successfully without a free function."""
    from pyroxide import load_dylib, compile_rust

    RUST_NO_FREE_SRC = """
    #[no_mangle]
    pub extern "C" fn add_ints(a: i32, b: i32) -> i32 {
        a + b
    }
    """

    compile_rust("dyn_no_free", RUST_NO_FREE_SRC)

    proxy = load_dylib(
        "dyn_no_free",
        signatures={
            "add_ints": {
                "args": ["i32", "i32"],
                "ret": "i32",
            }
        },
    )

    handle = proxy.add_ints(10, 20)
    assert handle.result() == 30


def test_dylib_no_free_raw_fail():
    """Verifies that raw bytes execution fails with RuntimeError if pyroxide_plugin_free is missing."""
    from pyroxide import load_dylib, compile_rust

    RUST_NO_FREE_SRC = """
    #[no_mangle]
    pub extern "C" fn add_ints(a: i32, b: i32) -> i32 {
        a + b
    }
    """

    compile_rust("dyn_no_free_raw", RUST_NO_FREE_SRC)

    proxy = load_dylib("dyn_no_free_raw", signatures={})

    with pytest.raises(Exception) as exc_info:
        proxy.add_ints(b"payload").result()
    assert "require the symbol 'pyroxide_plugin_free'" in str(exc_info.value)
