import shutil
import pytest
from pyroxide import compile_c, compile_zig, dylib_task

# Check compiler availability
cc_available = (
    shutil.which("cc") is not None
    or shutil.which("gcc") is not None
    or shutil.which("clang") is not None
)
zig_available = shutil.which("zig") is not None

C_SRC = """
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
    uint8_t* res = (uint8_t*)malloc(len);
    if (!res) {
        *out_len = 0;
        return NULL;
    }
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


@pytest.mark.skipif(not cc_available, reason="C compiler (cc/gcc/clang) not found")
def test_c_compile_and_run():
    """Test C code compilation and execution on-the-fly."""
    compile_c("c_upper", C_SRC)

    @dylib_task("c_upper")
    def run_c_upper(payload: str) -> str:
        pass

    handle = run_c_upper("hello from c language")
    result = handle.result()
    assert result == "HELLO FROM C LANGUAGE"


@pytest.mark.skipif(not zig_available, reason="Zig compiler (zig) not found")
def test_zig_compile_and_run():
    """Test Zig code compilation and execution on-the-fly."""
    compile_zig("zig_upper", ZIG_SRC)

    @dylib_task("zig_upper")
    def run_zig_upper(payload: str) -> str:
        pass

    handle = run_zig_upper("hello from zig language")
    result = handle.result()
    assert result == "HELLO FROM ZIG LANGUAGE"
