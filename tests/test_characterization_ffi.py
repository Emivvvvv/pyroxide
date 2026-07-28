import struct

import pytest
from pyroxide import compile_rust, load_dylib
from pyroxide._ffi_types import (
    FFI_STRUCT_CODES,
    SUPPORTED_FFI_TYPES,
    validate_ffi_type,
)


def test_ffi_types_parity_with_supported_set():
    expected_types = {"i32", "u32", "i64", "u64", "isize", "usize", "f32", "f64"}
    assert SUPPORTED_FFI_TYPES == expected_types
    assert set(FFI_STRUCT_CODES.keys()) == expected_types
    for t in expected_types:
        validate_ffi_type(t)


@pytest.fixture(scope="module")
def ffi_compiled_lib():
    rust_code = """
    #[no_mangle]
    pub extern "C" fn pyroxide_metadata() -> *const std::os::raw::c_char {
        b"identity_i32:i32|i32;identity_u32:u32|u32;identity_i64:i64|i64;identity_u64:u64|u64;identity_isize:isize|isize;identity_usize:usize|usize;identity_f32:f32|f32;identity_f64:f64|f64;add_two:i32,i32|i32\\0".as_ptr() as *const _
    }

    #[no_mangle]
    pub extern "C" fn identity_i32(v: i32) -> i32 { v }
    #[no_mangle]
    pub extern "C" fn identity_u32(v: u32) -> u32 { v }
    #[no_mangle]
    pub extern "C" fn identity_i64(v: i64) -> i64 { v }
    #[no_mangle]
    pub extern "C" fn identity_u64(v: u64) -> u64 { v }
    #[no_mangle]
    pub extern "C" fn identity_isize(v: isize) -> isize { v }
    #[no_mangle]
    pub extern "C" fn identity_usize(v: usize) -> usize { v }
    #[no_mangle]
    pub extern "C" fn identity_f32(v: f32) -> f32 { v }
    #[no_mangle]
    pub extern "C" fn identity_f64(v: f64) -> f64 { v }
    #[no_mangle]
    pub extern "C" fn add_two(a: i32, b: i32) -> i32 { a + b }
    """
    return compile_rust("char_ffi_lib", rust_code)


def test_all_eight_primitive_types_in_process(ffi_compiled_lib):
    proxy = load_dylib(ffi_compiled_lib, isolated=False)

    assert proxy.identity_i32(-2147483648).result() == -2147483648
    assert proxy.identity_i32(2147483647).result() == 2147483647

    assert proxy.identity_u32(0).result() == 0
    assert proxy.identity_u32(4294967295).result() == 4294967295

    assert proxy.identity_i64(-9223372036854775808).result() == -9223372036854775808
    assert proxy.identity_i64(9223372036854775807).result() == 9223372036854775807

    assert proxy.identity_u64(0).result() == 0
    assert proxy.identity_u64(18446744073709551615).result() == 18446744073709551615

    ptr_bits = struct.calcsize("P") * 8
    isize_max = (1 << (ptr_bits - 1)) - 1
    isize_min = -(1 << (ptr_bits - 1))
    usize_max = (1 << ptr_bits) - 1

    assert proxy.identity_isize(isize_min).result() == isize_min
    assert proxy.identity_isize(isize_max).result() == isize_max

    assert proxy.identity_usize(0).result() == 0
    assert proxy.identity_usize(usize_max).result() == usize_max

    assert abs(proxy.identity_f32(3.14159).result() - 3.14159) < 1e-4
    assert proxy.identity_f64(2.718281828459045).result() == 2.718281828459045


def test_all_eight_primitive_types_isolated(ffi_compiled_lib):
    proxy = load_dylib(ffi_compiled_lib, isolated=True)
    assert proxy.add_two(100, 200).result() == 300
    assert proxy.identity_u32(12345).result() == 12345
