import shutil
from pyroxide import compile_c, compile_rust, compile_zig, dylib_task

# Check compiler availability
cc_available = (
    shutil.which("cc") is not None
    or shutil.which("gcc") is not None
    or shutil.which("clang") is not None
)
rust_available = shutil.which("rustc") is not None
zig_available = shutil.which("zig") is not None

# 1. C Source
C_SRC = """
#include <stdint.h>
#include <stdlib.h>

uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
    uint8_t* res = (uint8_t*)malloc(len);
    for (size_t i = 0; i < len; i++) {
        res[i] = ptr[i] + 1; // Caesar shift
    }
    *out_len = len;
    return res;
}

void pyroxide_plugin_free(uint8_t* ptr, size_t len) {
    free(ptr);
}
"""

# 2. Rust Source
RUST_SRC = """
#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_run(ptr: *const u8, len: usize, out_len: *mut usize) -> *mut u8 {
    let input = std::slice::from_raw_parts(ptr, len);
    let mut res = input.to_vec();
    for x in &mut res {
        *x = x.wrapping_add(1);
    }
    *out_len = len;
    let boxed = res.into_boxed_slice();
    Box::into_raw(boxed) as *mut u8
}

#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_free(ptr: *mut u8, len: usize) {
    let _ = Box::from_raw(std::slice::from_raw_parts_mut(ptr, len));
}
"""

# 3. Zig Source
ZIG_SRC = """
const std = @import("std");

export fn pyroxide_plugin_run(ptr: [*]const u8, len: usize, out_len: *usize) ?[*]u8 {
    const gpa = std.heap.page_allocator;
    const input = ptr[0..len];
    var res = gpa.alloc(u8, len) catch return null;
    for (input, 0..) |val, i| {
        res[i] = val +% 1;
    }
    out_len.* = len;
    return res.ptr;
}

export fn pyroxide_plugin_free(ptr: [*]u8, len: usize) void {
    const gpa = std.heap.page_allocator;
    const slice = ptr[0..len];
    gpa.free(slice);
}
"""

if __name__ == "__main__":
    print("--- 8. Dynamic Native Compilers Example ---")
    
    # Compile and load C code
    if cc_available:
        print("Compiling C plugin on-the-fly...")
        compile_c("caesar_shift_c", C_SRC)
        
        @dylib_task("caesar_shift_c")
        def apply_c(payload: bytes) -> bytes:
            pass
        print(f"C Output:   {apply_c(b'abc').result()}")
    else:
        print("C compiler (cc/gcc/clang) not found. Skipping C Caesar shift example.")
 
    # Compile and load Rust code
    if rust_available:
        print("Compiling Rust plugin on-the-fly...")
        compile_rust("caesar_shift_rust", RUST_SRC)
        
        @dylib_task("caesar_shift_rust")
        def apply_rust(payload: bytes) -> bytes:
            pass
        print(f"Rust Output: {apply_rust(b'abc').result()}")
    else:
        print("Rust compiler (rustc) not found. Skipping Rust Caesar shift example.")
 
    # Compile and load Zig code
    if zig_available:
        print("Compiling Zig plugin on-the-fly...")
        compile_zig("caesar_shift_zig", ZIG_SRC)
        
        @dylib_task("caesar_shift_zig")
        def apply_zig(payload: bytes) -> bytes:
            pass
        print(f"Zig Output:  {apply_zig(b'abc').result()}")
    else:
        print("Zig compiler (zig) not found. Skipping Zig Caesar shift example.")
    
    print("✔ Dynamic Native Compilers PASSED.")

