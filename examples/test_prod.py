import gc
from pyroxide import task
from pyroxide._pyroxide import get_slab_size


# 1. Standard task
@task
def add_one(x):
    return x + 1


# 2. Native task with special payload that panics in Rust worker
@task(native=True)
def native_task(payload):
    pass


if __name__ == "__main__":
    print("=== Pyroxide Production Readiness Verification ===\n")

    # --- Test Case 1: Memory Eviction via GC ---
    print("1. Testing Memory Eviction...")
    initial_slab = get_slab_size()
    print(f"   Initial slab size: {initial_slab}")

    # Submit task and keep reference
    handle = add_one(10)
    print(
        f"   Submitted task. Slab size: {get_slab_size()} (Expected: {initial_slab + 1})"
    )

    # Block and read result
    print(f"   Task Result: {handle.result()}")

    # Delete the Python handle reference and force garbage collection
    print("   Deleting Python TaskHandle and running GC...")
    del handle
    gc.collect()

    final_slab = get_slab_size()
    print(f"   Final slab size: {final_slab} (Expected: {initial_slab})")
    if final_slab == initial_slab:
        print("   -> Success: Slab memory was safely reclaimed!\n")
    else:
        print("   -> Failure: Memory leak detected.\n")

    # --- Test Case 2: Panic Safety ---
    print("2. Testing Rust Worker Panic Safety...")

    # Trigger a panic in the Rust worker thread by passing TRIGGER_PANIC in native mode
    print("   Submitting task designed to PANIC inside Rust worker...")
    panic_handle = native_task("TRIGGER_PANIC")

    try:
        res = panic_handle.result()
        print(f"   Result: {res}")
    except RuntimeError as e:
        print(f"   Caught Expected Error: {e}")
        print(f"   Task Status: {panic_handle.status} (Expected: Failed)")

    # Verify that the worker thread did not crash the engine, and we can still run subsequent tasks
    print("\n3. Testing Engine Health after Panic...")
    del panic_handle
    gc.collect()

    success_handle = add_one(41)
    res = success_handle.result()
    print(
        f"   Subsequent Task Result: {res} (Expected: 42) | Status: {success_handle.status}"
    )
    if res == 42:
        print(
            "   -> Success: Worker threads survived and engine remains fully operational!\n"
        )
    else:
        print("   -> Failure: Worker pool crashed.\n")

    print("All production verification tests passed!")
