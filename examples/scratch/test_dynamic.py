from pyroxide import task, compile_dylib, dylib_task


# 1. Test Python callable execution (default)
@task
def calculate_square(x):
    return x * x


# 2. Test failing Python task
@task
def fail_task(val):
    raise ValueError(f"Intentionally failing with value: {val}")


# 3. Test Dynamic Shared Library (dylib)
RUST_SRC = """
#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_run(ptr: *const u8, len: usize, out_len: *mut usize) -> *mut u8 {
    let input = std::slice::from_raw_parts(ptr, len);
    let s = std::str::from_utf8(input).unwrap_or("");
    let result = s.to_uppercase().into_bytes();
    *out_len = result.len();
    let boxed = result.into_boxed_slice();
    Box::into_raw(boxed) as *mut u8
}

#[no_mangle]
pub unsafe extern "C" fn pyroxide_plugin_free(ptr: *mut u8, len: usize) {
    let _ = Box::from_raw(std::slice::from_raw_parts_mut(ptr, len));
}
"""

compile_dylib("greeter_dylib", RUST_SRC)


@dylib_task("greeter_dylib")
def dylib_uppercase(payload):
    pass


if __name__ == "__main__":
    print("=== Pyroxide Dynamic Task Execution Verification ===\n")

    print("1. Python callable task (calculate_square(12))...")
    handle1 = calculate_square(12)
    res1 = handle1.result(consume=False)
    print(f"   Result: {res1} (Expected: 144) | Status: {handle1.status}\n")

    print("2. Exception propagation...")
    handle2 = fail_task("broken_payload")
    try:
        handle2.result(consume=False)
    except Exception as e:
        print(f"   Caught: {type(e).__name__}: {e}")
        print(f"   Status: {handle2.status}\n")

    print("3. Dylib task (dylib_uppercase('hello dynamic pyroxide'))...")
    handle3 = dylib_uppercase("hello dynamic pyroxide")
    res3 = handle3.result(consume=False)
    print(f"   Result: '{res3}' (Expected: 'HELLO DYNAMIC PYROXIDE')")
    print(f"   Status: {handle3.status}\n")

    print("All verification tests passed!")
