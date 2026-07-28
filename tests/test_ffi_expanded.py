import struct

import pytest
from pyroxide import compile_rust, load_dylib
from pyroxide._ffi_types import (
    FFI_PYTHON_TYPES,
    FFI_STRUCT_CODES,
    build_argument_format,
    build_return_format,
    validate_ffi_type,
)


def test_ffi_types_struct_codes():
    assert FFI_STRUCT_CODES["i32"] == "i"
    assert FFI_STRUCT_CODES["u32"] == "I"
    assert FFI_STRUCT_CODES["i64"] == "q"
    assert FFI_STRUCT_CODES["u64"] == "Q"
    assert FFI_STRUCT_CODES["f32"] == "f"
    assert FFI_STRUCT_CODES["f64"] == "d"

    ptr_size = struct.calcsize("P")
    if ptr_size == 8:
        assert FFI_STRUCT_CODES["isize"] == "q"
        assert FFI_STRUCT_CODES["usize"] == "Q"
    elif ptr_size == 4:
        assert FFI_STRUCT_CODES["isize"] == "i"
        assert FFI_STRUCT_CODES["usize"] == "I"

    for ffi_type, py_type in FFI_PYTHON_TYPES.items():
        if "f" in ffi_type:
            assert py_type == "float"
        else:
            assert py_type == "int"


def test_ffi_type_validation_and_formats():
    validate_ffi_type("u32")
    validate_ffi_type("usize")

    with pytest.raises(ValueError, match="Unsupported FFI type 'uint32'"):
        validate_ffi_type("uint32")

    arg_fmt = build_argument_format(["u32", "f64", "usize"])
    assert arg_fmt.startswith("=")
    assert build_return_format("u64") == "=Q"


@pytest.fixture(scope="module")
def test_ffi_lib():
    rust_code = """
    #[no_mangle]
    pub extern "C" fn pyroxide_metadata() -> *const std::os::raw::c_char {
        b"current_u64:|u64;identity_u32:u32|u32;identity_usize:usize|usize;add_u64:u64,u64|u64;mixed_binary:u32,f64|usize;triple_u32:u32,u32,u32|u64;mixed_three:i32,i32,f64|f64;four_u64:u64,u64,u64,u64|u64;eight_i32:i32,i32,i32,i32,i32,i32,i32,i32|i64\\0".as_ptr() as *const _
    }

    #[no_mangle]
    pub extern "C" fn current_u64() -> u64 {
        18446744073709551615
    }

    #[no_mangle]
    pub extern "C" fn identity_u32(val: u32) -> u32 {
        val
    }

    #[no_mangle]
    pub extern "C" fn identity_usize(val: usize) -> usize {
        val
    }

    #[no_mangle]
    pub extern "C" fn add_u64(a: u64, b: u64) -> u64 {
        a + b
    }

    #[no_mangle]
    pub extern "C" fn mixed_binary(a: u32, b: f64) -> usize {
        (a as usize) + (b as usize)
    }

    #[no_mangle]
    pub extern "C" fn triple_u32(a: u32, b: u32, c: u32) -> u64 {
        (a as u64) + (b as u64) + (c as u64)
    }

    #[no_mangle]
    pub extern "C" fn mixed_three(a: i32, b: i32, c: f64) -> f64 {
        (a as f64) + (b as f64) + c
    }

    #[no_mangle]
    pub extern "C" fn four_u64(a: u64, b: u64, c: u64, d: u64) -> u64 {
        a + b + c + d
    }

    #[no_mangle]
    pub extern "C" fn eight_i32(
        a: i32, b: i32, c: i32, d: i32, e: i32, f: i32, g: i32, h: i32
    ) -> i64 {
        (a as i64) + (b as i64) + (c as i64) + (d as i64) +
        (e as i64) + (f as i64) + (g as i64) + (h as i64)
    }
    """
    path = compile_rust("ffi_expanded_test", rust_code)
    return path


def test_ffi_zero_arg_and_unsigned_boundaries(test_ffi_lib):
    proxy = load_dylib(
        test_ffi_lib,
        signatures={
            "current_u64": {"args": [], "ret": "u64"},
            "identity_u32": {"args": ["u32"], "ret": "u32"},
            "identity_usize": {"args": ["usize"], "ret": "usize"},
            "add_u64": {"args": ["u64", "u64"], "ret": "u64"},
            "mixed_binary": {"args": ["u32", "f64"], "ret": "usize"},
            "triple_u32": {"args": ["u32", "u32", "u32"], "ret": "u64"},
            "mixed_three": {"args": ["i32", "i32", "f64"], "ret": "f64"},
            "four_u64": {"args": ["u64", "u64", "u64", "u64"], "ret": "u64"},
            "eight_i32": {
                "args": ["i32", "i32", "i32", "i32", "i32", "i32", "i32", "i32"],
                "ret": "i64",
            },
        },
    )

    # Zero arg call
    assert proxy.current_u64().result() == 18446744073709551615

    # u32 boundaries
    assert proxy.identity_u32(0).result() == 0
    assert proxy.identity_u32(2147483648).result() == 2147483648
    assert proxy.identity_u32(4294967295).result() == 4294967295

    # u64 addition
    assert proxy.add_u64(4_000_000_000, 5_000_000_000).result() == 9_000_000_000

    # mixed binary
    assert proxy.mixed_binary(100, 20.5).result() == 120

    # triple u32
    assert proxy.triple_u32(10, 20, 30).result() == 60

    # mixed three
    assert proxy.mixed_three(5, -2, 10.5).result() == 13.5

    # four u64
    assert proxy.four_u64(1, 2, 3, 4).result() == 10

    # eight i32
    assert proxy.eight_i32(1, 2, 3, 4, 5, 6, 7, 8).result() == 36


def test_ffi_batch_and_isolated_execution(test_ffi_lib):
    proxy_iso = load_dylib(
        test_ffi_lib,
        signatures={
            "identity_u32": {"args": ["u32"], "ret": "u32"},
            "add_u64": {"args": ["u64", "u64"], "ret": "u64"},
        },
        isolated=True,
    )

    assert proxy_iso.identity_u32(42).result() == 42
    assert proxy_iso.add_u64(100, 200).result() == 300

    # Batch calls
    handles = proxy_iso.add_u64.batch([(1, 2), (10, 20), (100, 200)])
    results = [h.result() for h in handles]
    assert results == [3, 30, 300]


def test_ffi_range_and_arity_errors(test_ffi_lib):
    proxy = load_dylib(
        test_ffi_lib,
        signatures={
            "identity_u32": {"args": ["u32"], "ret": "u32"},
        },
    )

    # Negative unsigned value error
    with pytest.raises(ValueError, match="range or type error"):
        proxy.identity_u32(-1)

    # Overflow unsigned value error
    with pytest.raises(ValueError, match="range or type error"):
        proxy.identity_u32(4294967296)

    # Wrong argument count error
    with pytest.raises(ValueError, match="expects 1 arguments, received 2"):
        proxy.identity_u32(10, 20)


def test_ffi_unsupported_high_arity_mixed_shape():
    # Mixed 5-argument shape is unsupported (only homogeneous is supported for arities 5-8)
    rust_code = """
    #[no_mangle]
    pub extern "C" fn pyroxide_metadata() -> *const std::os::raw::c_char {
        b"mixed5:u32,f64,usize,i32,u64|u64\\0".as_ptr() as *const _
    }

    #[no_mangle]
    pub extern "C" fn mixed5(a: u32, b: f64, c: usize, d: i32, e: u64) -> u64 {
        100
    }
    """
    path = compile_rust("ffi_unsupported_test", rust_code)
    proxy = load_dylib(
        path,
        signatures={
            "mixed5": {
                "args": ["u32", "f64", "usize", "i32", "u64"],
                "ret": "u64",
            },
        },
    )

    with pytest.raises(RuntimeError, match="Unsupported FFI signature mapping"):
        proxy.mixed5(1, 2.0, 3, 4, 5).result()


def test_ffi_additional_types_and_extreme_values():
    rust_code = """
    #[no_mangle]
    pub extern "C" fn pyroxide_metadata() -> *const std::os::raw::c_char {
        b"identity_u64:u64|u64;identity_usize:usize|usize;identity_isize:isize|isize;identity_f32:f32|f32;zero_arg:|u64\\0".as_ptr() as *const _
    }

    #[no_mangle]
    pub extern "C" fn identity_u64(val: u64) -> u64 { val }

    #[no_mangle]
    pub extern "C" fn identity_usize(val: usize) -> usize { val }

    #[no_mangle]
    pub extern "C" fn identity_isize(val: isize) -> isize { val }

    #[no_mangle]
    pub extern "C" fn identity_f32(val: f32) -> f32 { val }

    #[no_mangle]
    pub extern "C" fn zero_arg() -> u64 { 42 }
    """
    path = compile_rust("ffi_comprehensive_types", rust_code)
    proxy = load_dylib(path)

    # u64 MAX
    u64_max = 18446744073709551615
    assert proxy.identity_u64(u64_max).result() == u64_max

    # usize MAX
    usize_max = (1 << (struct.calcsize("P") * 8)) - 1
    assert proxy.identity_usize(usize_max).result() == usize_max

    # isize MIN and MAX
    isize_max = (1 << (struct.calcsize("P") * 8 - 1)) - 1
    isize_min = -(1 << (struct.calcsize("P") * 8 - 1))
    assert proxy.identity_isize(isize_max).result() == isize_max
    assert proxy.identity_isize(isize_min).result() == isize_min

    # f32
    assert abs(proxy.identity_f32(3.14159).result() - 3.14159) < 1e-4

    # u64 negative and overflow
    with pytest.raises(ValueError, match="range or type error"):
        proxy.identity_u64(-1)
    with pytest.raises(ValueError, match="range or type error"):
        proxy.identity_u64(u64_max + 1)

    # usize negative and overflow
    with pytest.raises(ValueError, match="range or type error"):
        proxy.identity_usize(-1)
    with pytest.raises(ValueError, match="range or type error"):
        proxy.identity_usize(usize_max + 1)

    # zero arg batch calls
    batch_handles = proxy.zero_arg.batch([(), ()])
    assert [h.result() for h in batch_handles] == [42, 42]

    # result_async
    async def run_async():
        handle = proxy.identity_u64(12345)
        return await handle.result_async()

    import asyncio

    assert asyncio.run(run_async()) == 12345

    # Cache hit verification (invoking 100 times)
    for i in range(100):
        assert proxy.identity_u64(i).result() == i


def test_ffi_stub_generation_errors():
    from pyroxide.stubs import generate_stubs

    rust_bad_arg = """
    #[no_mangle]
    pub extern "C" fn pyroxide_metadata() -> *const std::os::raw::c_char {
        b"bad_func:invalid_type|u32\\0".as_ptr() as *const _
    }

    #[no_mangle]
    pub extern "C" fn bad_func() -> u32 { 0 }
    """
    path1 = compile_rust("ffi_stub_bad_arg", rust_bad_arg)
    load_dylib(path1)
    with pytest.raises(ValueError, match="Unsupported FFI type 'invalid_type'"):
        generate_stubs(path1, "dylib")

    rust_bad_ret = """
    #[no_mangle]
    pub extern "C" fn pyroxide_metadata() -> *const std::os::raw::c_char {
        b"bad_ret_func:u32|bad_ret\\0".as_ptr() as *const _
    }

    #[no_mangle]
    pub extern "C" fn bad_ret_func(val: u32) -> u32 { val }
    """
    path2 = compile_rust("ffi_stub_bad_ret", rust_bad_ret)
    load_dylib(path2)
    with pytest.raises(ValueError, match="Unsupported FFI type 'bad_ret'"):
        generate_stubs(path2, "dylib")


def test_ffi_independent_library_registrations():
    # Verify that independent library registrations do not share cached thunk calls
    rust_code1 = """
    #[no_mangle]
    pub extern "C" fn pyroxide_metadata() -> *const std::os::raw::c_char {
        b"value:|u32\\0".as_ptr() as *const _
    }

    #[no_mangle]
    pub extern "C" fn value() -> u32 { 100 }
    """
    rust_code2 = """
    #[no_mangle]
    pub extern "C" fn pyroxide_metadata() -> *const std::os::raw::c_char {
        b"value:|u32\\0".as_ptr() as *const _
    }

    #[no_mangle]
    pub extern "C" fn value() -> u32 { 200 }
    """
    path1 = compile_rust("ffi_reregister_test1", rust_code1)
    proxy1 = load_dylib(path1)
    assert proxy1.value().result() == 100

    path2 = compile_rust("ffi_reregister_test2", rust_code2)
    proxy2 = load_dylib(path2)
    assert proxy2.value().result() == 200
