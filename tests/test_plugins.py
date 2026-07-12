import pytest
import os
from pyroxide import compile_dylib, dylib_task

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

    compile_dylib("dyn_greeter", RUST_PLUGIN_SRC)

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
    compile_dylib("dyn_bytes", RUST_PLUGIN_SRC)

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
        compile_dylib("dyn_bad", bad_src)
    assert "Failed to compile dylib" in str(exc_info.value)
