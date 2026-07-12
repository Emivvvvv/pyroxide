from pyroxide import task


# 1. Test Python callable execution (default)
@task
def calculate_square(x):
    # This runs in the background thread (Rust worker pool)
    return x * x


# 2. Test failing Python task
@task
def fail_task(val):
    raise ValueError(f"Intentionally failing with value: {val}")


# 3. Test Native Rust task
@task(native=True)
def native_uppercase(payload):
    pass  # Managed natively by Rust


if __name__ == "__main__":
    print("=== Pyroxide Dynamic Task Execution Verification ===\n")

    # --- Test Case 1: Python Callable ---
    print("1. Submitting Python callable task (calculate_square(12))...")
    handle1 = calculate_square(12)
    print(f"   Task submitted. Status: {handle1.status}")
    res1 = handle1.result(consume=False)
    print(f"   Result: {res1} (Expected: 144) | Final Status: {handle1.status}\n")

    # --- Test Case 2: Exception Propagation ---
    print("2. Submitting Python callable task that raises exception...")
    handle2 = fail_task("broken_payload")
    try:
        handle2.result(consume=False)
    except Exception as e:
        print(f"   Successfully caught exception: {type(e).__name__}: {e}")
        print(f"   Final Status: {handle2.status}\n")

    # --- Test Case 3: Native Rust Execution ---
    print(
        "3. Submitting Native Rust task (native_uppercase('hello dynamic pyroxide'))..."
    )
    handle3 = native_uppercase("hello dynamic pyroxide")
    res3 = handle3.result(consume=False)
    print(
        f"   Result: '{res3.decode() if isinstance(res3, bytes) else res3}' (Expected: 'HELLO DYNAMIC PYROXIDE')"
    )
    print(f"   Final Status: {handle3.status}\n")

    print("Verification completed successfully!")
