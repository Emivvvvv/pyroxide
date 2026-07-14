import pytest
import os
import sys
import asyncio
import tempfile
import shutil
import time
import threading
from pyroxide import (
    register_wasm_wat,
    wasm_task,
    load_wasm,
    compile_c,
    generate_stubs,
    TaskHandle
)

# Global event to block workers cleanly
_worker_block_event = threading.Event()

# 1. WASM Resource Limits
def test_wasm_epoch_timeout():
    # An infinite loop in WAT
    wat_code = """
    (module
      (func (export "run") (param i32 i32) (result i64)
        (loop $l
          br $l)
        (i64.const 0))
      (func (export "alloc") (param i32) (result i32)
        (i32.const 0))
      (func (export "dealloc") (param i32 i32))
      (memory (export "memory") 1)
    )
    """
    os.environ["PYROXIDE_WASM_TIMEOUT_MS"] = "50"
    os.environ["PYROXIDE_WASM_TICK_MS"] = "5"
    register_wasm_wat("infinite_loop", wat_code)
    
    @wasm_task("infinite_loop", "run")
    def loop_task(payload: str) -> str:
        pass
        
    handle = loop_task("hello")
    with pytest.raises(RuntimeError) as exc_info:
        handle.result()
    assert "interrupt" in str(exc_info.value) or "WASM execution failed" in str(exc_info.value)


def test_wasm_memory_limit():
    # Grows memory past the limit (limit is 100MB by default, 1 page is 64KB)
    # 2000 pages is ~130MB.
    wat_code = """
    (module
      (func (export "run") (param i32 i32) (result i64)
        (if (i32.lt_s (memory.grow (i32.const 2000)) (i32.const 0))
          (then (unreachable))
        )
        (i64.const 0))
      (func (export "alloc") (param i32) (result i32)
        (i32.const 0))
      (func (export "dealloc") (param i32 i32))
      (memory (export "memory") 1)
    )
    """
    os.environ["PYROXIDE_WASM_MEMORY_LIMIT_BYTES"] = "10000000"  # ~10MB limit
    register_wasm_wat("memory_limit", wat_code)
    
    @wasm_task("memory_limit", "run")
    def mem_task(payload: str) -> str:
        pass
        
    handle = mem_task("hello")
    with pytest.raises(RuntimeError) as exc_info:
        handle.result()
    assert "WASM execution failed" in str(exc_info.value) or "unreachable" in str(exc_info.value) or "memory" in str(exc_info.value)


# 2. Queue Exhaustion
def test_queue_exhaustion():
    # Set timeout to 1ms so we raise error fast when full
    os.environ["PYROXIDE_QUEUE_TIMEOUT_MS"] = "1"
    
    # Define tasks
    from pyroxide import task
    
    @task
    def blocking_task(x):
        _worker_block_event.wait()
        return x
        
    @task
    def dummy_task(x):
        return x
        
    _worker_block_event.clear()
    handles = []
    # Block all workers (typically 4 or 8 threads)
    for i in range(16):
        handles.append(blocking_task(i))
        
    full = False
    try:
        # Flooding the queue while workers are blocked
        for i in range(11000):
            handles.append(dummy_task(i))
    except BufferError as e:
        full = True
        assert "Task queue is full" in str(e)
    finally:
        # Release the event so all blocked threads can continue
        _worker_block_event.set()
        # Restore timeout
        os.environ["PYROXIDE_QUEUE_TIMEOUT_MS"] = "1000"
        # Clean up tasks to prevent memory leaks in testing
        for h in handles:
            try:
                h.cancel()
            except Exception:
                pass
        # Give workers time to drain the cancelled tasks from the channel
        time.sleep(0.5)
    assert full is True


# 3. Compiler Disk Cleanup
def test_compiler_cleanup():
    c_source = """
    #include <stdint.h>
    #include <stdlib.h>
    #include <string.h>

    uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
        uint8_t* out = (uint8_t*)malloc(len);
        memcpy(out, ptr, len);
        *out_len = len;
        return out;
    }

    void pyroxide_plugin_free(uint8_t* ptr, size_t len) {
        free(ptr);
    }
    """
    temp_dir_before = os.listdir(tempfile.gettempdir())
    
    lib_path = compile_c("test_cleanup_lib", c_source)
    assert "/.pyroxide/cache/" in lib_path
    assert os.path.exists(lib_path)
    
    # Check that temporary compiler directories have been cleaned up
    temp_dir_after = os.listdir(tempfile.gettempdir())
    for name in temp_dir_after:
        if name not in temp_dir_before and name.startswith("pyroxide_c_"):
            assert False, f"Temp compiler directory leaked: {name}"


# 4. Native Async Waker
def test_native_async_waker():
    if sys.platform == "win32":
        pytest.skip("Pipe async waker not supported on Windows")
        
    from pyroxide import task
    @task
    def async_dummy_task(x):
        return x
        
    async def run():
        handle = async_dummy_task("async_test")
        res = await handle.result_async()
        return res
        
    res = asyncio.run(run())
    assert res == "async_test"


# 5. Fix Stub Imports
def test_stub_imports_generation():
    # Make sure we clean up any previous generated files
    stub_pyi = "my_stub_proxy.pyi"
    stub_py = "my_stub_proxy.py"
    if os.path.exists(stub_pyi):
        os.remove(stub_pyi)
    if os.path.exists(stub_py):
        os.remove(stub_py)
        
    # We will register a dummy module so we can generate stubs for it
    c_source = """
    #include <stdint.h>
    #include <stdlib.h>
    #include <string.h>

    uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
        uint8_t* out = (uint8_t*)malloc(len);
        memcpy(out, ptr, len);
        *out_len = len;
        return out;
    }

    void pyroxide_plugin_free(uint8_t* ptr, size_t len) {
        free(ptr);
    }
    """
    compile_c("my_stub", c_source)
    
    generate_stubs("my_stub", "dylib", out_path=stub_pyi)
    
    assert os.path.exists(stub_pyi)
    assert os.path.exists(stub_py)
    
    # Verify that we can import and load the proxy class from the generated .py file
    sys.path.insert(0, os.getcwd())
    try:
        from my_stub_proxy import load_dylib_my_stub, My_stubDylibProxy
        proxy = load_dylib_my_stub()
        assert proxy is not None
    finally:
        sys.path.pop(0)
        if os.path.exists(stub_pyi):
            os.remove(stub_pyi)
        if os.path.exists(stub_py):
            os.remove(stub_py)


# 6. OOP Proxy Batching
def test_oop_proxy_batch():
    c_source = """
    #include <stdint.h>
    #include <stdlib.h>
    #include <string.h>

    uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
        uint8_t* out = (uint8_t*)malloc(len);
        memcpy(out, ptr, len);
        *out_len = len;
        return out;
    }

    void pyroxide_plugin_free(uint8_t* ptr, size_t len) {
        free(ptr);
    }
    """
    compile_c("batch_oop_lib", c_source)
    from pyroxide import load_dylib
    proxy = load_dylib("batch_oop_lib")
    
    # Test batch call
    handles = proxy.pyroxide_plugin_run.batch(["payload1", "payload2"])
    assert len(handles) == 2
    assert handles[0].result() == "payload1"
    assert handles[1].result() == "payload2"


# 7. Avoid Sync Status Poll on __repr__
def test_repr_no_poll():
    from pyroxide import task
    @task
    def dummy_task(x):
        return x
    handle = dummy_task("repr_test")
    
    # repr should just output ID and not status
    rep = repr(handle)
    assert f"id={handle.task_id}" in rep
    assert "status=" not in rep
