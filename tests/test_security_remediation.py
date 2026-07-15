import pytest
import os
import sys
import asyncio
import time
from pyroxide import task, compile_c, load_dylib, group, TaskHandle
from pyroxide.workflows import ExceptionGroup

def test_exception_group_compatibility():
    # Verify that ExceptionGroup is defined and doesn't crash on this Python version
    assert ExceptionGroup is not None
    try:
        raise ExceptionGroup("Test group", [ValueError("Err 1"), TypeError("Err 2")])
    except ExceptionGroup as eg:
        assert eg.args[0] == "Test group"
        assert len(eg.exceptions) == 2
        assert isinstance(eg.exceptions[0], ValueError)
        assert isinstance(eg.exceptions[1], TypeError)

def test_negative_timeout_validation():
    @task
    def simple_task(x):
        return x

    handle = simple_task("test")
    with pytest.raises(ValueError, match="timeout_sec must be non-negative"):
        handle.wait(timeout_sec=-5.0)
        
    handle.result() # Cleanup

def test_ffi_signature_length_validation():
    # Compile a simple C library with a numeric signature function
    c_source = """
    #include <stdint.h>
    int32_t add_numbers(int32_t a, int32_t b) {
        return a + b;
    }
    """
    lib_path = compile_c("test_ffi_val", c_source)
    
    # Load dylib with signature (2 * i32 = 8 bytes)
    math_lib = load_dylib("test_ffi_val", signatures={
        "add_numbers": {"args": ["i32", "i32"], "ret": "i32"}
    })
    
    # Correct call (args are packed into 8 bytes)
    handle = math_lib.add_numbers(10, 20)
    assert handle.result() == 30
    
    # Now let's try calling with manually mismatched payload via raw internal FFI
    from pyroxide._pyroxide import submit_dylib_task
    # Signature expects 8 bytes, we send 4 bytes
    with pytest.raises(RuntimeError) as exc_info:
        task_id = submit_dylib_task(
            "test_ffi_val",
            "add_numbers",
            b"\x01\x00\x00\x00",  # 4 bytes
            ffi_sig=(["i32", "i32"], "i32")
        )
        # Block to get execution result
        h = TaskHandle(task_id)
        h.result()
    assert "Payload length mismatch" in str(exc_info.value)

def test_compilation_lock_concurrency():
    # Make sure multiple rapid calls to compile do not trigger file collisions
    c_source = """
    #include <stdint.h>
    #include <stdlib.h>
    #include <string.h>
    uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
        *out_len = len;
        return (uint8_t*)ptr;
    }
    void pyroxide_plugin_free(uint8_t* ptr, size_t len) {}
    """
    
    import threading
    errors = []
    
    def compile_job(i):
        try:
            # Compile unique library names concurrently to prevent macOS dlopen overwrite rejection,
            # while still competing for the same global compilation lock
            compile_c(f"concur_lib_{i}", c_source)
        except Exception as e:
            errors.append(e)
            
    threads = [threading.Thread(target=compile_job, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    # None of the compiles should fail or corrupt files because they are protected by locks
    assert len(errors) == 0

def test_empty_payload_rejection():
    c_source = """
    #include <stdint.h>
    #include <stdlib.h>
    #include <string.h>
    uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
        *out_len = len;
        return (uint8_t*)ptr;
    }
    void pyroxide_plugin_free(uint8_t* ptr, size_t len) {}
    """
    compile_c("empty_payload_lib", c_source)
    from pyroxide import load_dylib
    lib = load_dylib("empty_payload_lib")
    
    # Running raw dylib task with empty payload should raise a rejection error
    with pytest.raises(RuntimeError, match="Payload cannot be empty"):
        lib.pyroxide_plugin_run("").result()

def test_symbol_cache_pollution_prevention():
    # We will compile a single function and call it via raw FFI and via custom FFI
    # to ensure they do not pollute/collide in the symbol cache
    c_source = """
    #include <stdint.h>
    #include <stdlib.h>
    #include <string.h>
    
    // Custom FFI function
    int32_t add_one(int32_t x) {
        return x + 1;
    }
    
    // Raw binary function matching the same name (so we can test symbol cache collision)
    // Wait, in C we cannot have two functions with the same name in the same library.
    // But we can register the same library name, compile a custom function, and load it as FFI,
    // and then call it using execute_dylib or vice versa to see if it doesn't crash on invalid cast.
    """
    lib_path = compile_c("cache_pollute_lib", c_source)
    
    # Load dylib with custom signature
    lib = load_dylib("cache_pollute_lib", signatures={
        "add_one": {"args": ["i32"], "ret": "i32"}
    })
    
    # Call as FFI (this will cache it with signature)
    assert lib.add_one(41).result() == 42
    
    # Now call the same symbol raw using internal execute_dylib (this has no signature)
    # It should fail cleanly due to signature mismatch or lookup (it is looked up with a different key)
    # instead of transmuting and executing the invalid function pointer as a raw PluginRunFn
    from pyroxide._pyroxide import submit_dylib_task
    with pytest.raises(RuntimeError):
        # We submit it as a raw binary task (no ffi_sig)
        task_id = submit_dylib_task(
            "cache_pollute_lib",
            "add_one",
            b"hello"
        )
        h = TaskHandle(task_id)
        h.result()

def test_cancelled_task_wait_runtime_error():
    # Calling wait() on a cancelled task with a timeout should raise RuntimeError, not TimeoutError
    @task
    def long_task(x):
        time.sleep(0.5)
        return x
        
    handle = long_task("val")
    time.sleep(0.02)
    handle.cancel()
    
    with pytest.raises(RuntimeError, match="Task cancelled"):
        handle.wait(timeout_sec=1.0)

def test_compilation_lock_self_healing():
    from pyroxide.plugins import CrossProcessLock
    import tempfile
    
    with tempfile.TemporaryDirectory() as tempdir:
        lock_dir = os.path.join(tempdir, "my_lock")
        lock1 = CrossProcessLock(lock_dir)
        
        # Manually create stale lock directory with a non-existent PID
        os.makedirs(lock_dir)
        pid_file = os.path.join(lock_dir, "owner.pid")
        with open(pid_file, "w") as f:
            # PID 999999 is highly unlikely to be running
            f.write("999999")
            
        # Attempt to acquire lock. It should detect dead PID, remove it, and succeed
        lock2 = CrossProcessLock(lock_dir, timeout=2.0)
        assert lock2.acquire() is True
        
        # Clean up
        lock2.release()

def test_task_group_cancellation_filtering():
    @task
    def failing_task(x):
        raise ValueError("Root error")
        
    @task
    def sleeping_task(x):
        time.sleep(0.5)
        return x
        
    async def run():
        h1 = failing_task(1)
        h2 = sleeping_task(2)
        
        with pytest.raises(ExceptionGroup) as exc_info:
            async with group([h1, h2]):
                pass
                
        exceptions = exc_info.value.exceptions
        # Root error should be present
        assert any(isinstance(e, RuntimeError) and "Root error" in str(e) for e in exceptions)
        # Sibling task cancellation errors should be filtered out to reduce noise
        assert not any(isinstance(e, RuntimeError) and "cancelled" in str(e).lower() for e in exceptions)
        
    asyncio.run(run())

def test_multi_loop_concurrency():
    if sys.platform == "win32":
        pytest.skip("Pipe async waker not supported on Windows")

    import threading
    results = {}

    @task
    def async_worker_task(x):
        return x * 2

    def run_loop_in_thread(tid):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def run():
            h = async_worker_task(tid)
            res = await h.result_async()
            results[tid] = res
            
        loop.run_until_complete(run())
        loop.close()

    threads = [threading.Thread(target=run_loop_in_thread, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All threads must successfully finish and set their results without hanging
    for i in range(4):
        assert results[i] == i * 2

@task
def fast_non_isolated_task(x):
    return x

def test_isolated_task_concurrency_no_starvation():
    from tests.isolated_helper import long_isolated_task_helper
    # Spawn isolated tasks
    handles = [long_isolated_task_helper(i) for i in range(8)]
    time.sleep(0.05)

    # Spawn fast task. It should run and complete immediately because worker pool is not starved
    fast_handle = fast_non_isolated_task(100)
    assert fast_handle.result(timeout_sec=1.0) == 100

    # Cleanup
    for h in handles:
        try:
            h.cancel()
        except Exception:
            pass

def test_shared_memory_no_leaks():
    if sys.platform == "win32":
        pytest.skip("SHM leak checks Unix-specific")

    from tests.isolated_helper import large_payload_task_helper
    # Create a 1.2MB payload to force SHM routing
    large_payload = "a" * (1200 * 1024)
    
    # Check what was in /dev/shm before
    shm_before = set()
    if os.path.exists("/dev/shm"):
        shm_before = set(os.listdir("/dev/shm"))

    handle = large_payload_task_helper(large_payload)
    res = handle.result()
    assert len(res) == 100

    # Ensure no new shm files leaked in /dev/shm
    if os.path.exists("/dev/shm"):
        time.sleep(0.1)  # Give OS a millisecond to delete unlinked file descriptors
        shm_after = set(os.listdir("/dev/shm"))
        leaked = [f for f in shm_after if f not in shm_before and "pyroxide_shm" in f]
        assert len(leaked) == 0, f"SHM segments leaked in /dev/shm: {leaked}"

def test_unregister_dylib():
    from pyroxide import compile_c, unregister_dylib, load_dylib
    
    c_source = """
    #include <stdint.h>
    int32_t multiply_numbers(int32_t a, int32_t b) {
        return a * b;
    }
    """
    compile_c("temp_unregister_lib", c_source)
    
    # Verify it loads successfully
    lib = load_dylib("temp_unregister_lib", signatures={
        "multiply_numbers": {"args": ["i32", "i32"], "ret": "i32"}
    })
    assert lib.multiply_numbers(6, 7).result() == 42
    
    # Now unregister it
    unregister_dylib("temp_unregister_lib")
    
    # Loading and calling it again should raise an error because it is unregistered
    lib2 = load_dylib("temp_unregister_lib", signatures={
        "multiply_numbers": {"args": ["i32", "i32"], "ret": "i32"}
    })
    with pytest.raises(RuntimeError):
        lib2.multiply_numbers(6, 7).result()


def test_custom_deallocator():
    from pyroxide import compile_c, load_dylib

    c_source = """
    #include <stdlib.h>
    #include <string.h>
    #include <stdint.h>

    uint8_t* custom_run_fn(const uint8_t* payload, size_t len, size_t* out_len) {
        *out_len = len + 1;
        uint8_t* buf = (uint8_t*)malloc(*out_len);
        memcpy(buf, payload, len);
        buf[len] = '!';
        return buf;
    }

    void my_custom_free(uint8_t* ptr, size_t len) {
        free(ptr);
    }
    """
    compile_c("test_custom_dealloc_lib", c_source)

    # Load with custom deallocator free_fn_name
    lib = load_dylib("test_custom_dealloc_lib", free_fn_name="my_custom_free")

    # Run the raw binary task and verify it appends '!'
    h = lib.custom_run_fn(b"hello")
    assert h.result() == b"hello!"


