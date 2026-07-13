import pytest
import concurrent.futures
from pyroxide import register_wasm, wasm_task, compile_c, dylib_task
from tests.isolated_helper import square_isolated, crash_task, echo_large_payload, get_worker_pid

# 1. Test basic Python isolated execution
def test_isolated_python_task():
    handle = square_isolated(9)
    assert handle.result() == 81

# 2. Test crash safety (Process Exit)
def test_isolated_crash_safety():
    handle = crash_task(0)
    with pytest.raises(RuntimeError) as exc_info:
        handle.result()
    err_msg = str(exc_info.value).lower()
    assert "crashed" in err_msg or "eof" in err_msg or "broken pipe" in err_msg or "connection reset" in err_msg

# 3. Test post-crash pool recovery
def test_isolated_pool_recovery():
    # Crash the worker first
    handle1 = crash_task(0)
    with pytest.raises(RuntimeError):
        handle1.result()
        
    # The pool should immediately heal and spawn a new worker for the next task
    handle2 = square_isolated(12)
    assert handle2.result() == 144

# 4. Test parallel concurrency with isolated workers
def test_isolated_concurrency():
    # Submit multiple isolated tasks concurrently using a ThreadPoolExecutor
    # to stress-test the process pool acquisition and release locks
    def run_task(val):
        return square_isolated(val).result()

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(run_task, i) for i in range(10)]
        results = [f.result() for f in futures]
        
    assert results == [i * i for i in range(10)]

# 5. Test WASM isolated execution
def test_isolated_wasm_task():
    from tests.test_wasm import WASM_BYTES
    register_wasm("rot13_isolated", WASM_BYTES)
    
    @wasm_task("rot13_isolated", "run", isolated=True)
    def rot13_cipher(payload: str) -> str:
        pass
        
    handle = rot13_cipher("Hello Isolated WASM!")
    assert handle.result() == "Uryyb Vfbyngrq JNFZ!"

# 6. Test dylib isolated execution
def test_isolated_dylib_task():
    C_SRC = """
    #include <stdint.h>
    #include <stdlib.h>
    uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
        uint8_t* res = (uint8_t*)malloc(len);
        for (size_t i = 0; i < len; i++) {
            res[i] = ptr[i] + 1; // Caesar cipher +1
        }
        *out_len = len;
        return res;
    }
    void pyroxide_plugin_free(uint8_t* ptr, size_t len) {
        free(ptr);
    }
    """
    compile_c("caesar_isolated", C_SRC)
    
    @dylib_task("caesar_isolated", isolated=True)
    def caesar_cipher(payload: bytes) -> bytes:
        pass
        
    handle = caesar_cipher(b"abc")
    assert handle.result() == b"bcd"



def test_isolated_large_payload_shm():
    # 1.5 MB payload
    large_data = "A" * (1024 * 1024 + 100 * 1024)
    handle = echo_large_payload(large_data)
    result = handle.result()
    assert len(result) == len(large_data)
    assert result == large_data



def test_isolated_scale_to_zero(monkeypatch):
    # Set idle timeout to 1 second for fast testing
    monkeypatch.setenv("PYROXIDE_IDLE_TIMEOUT_SEC", "1")
    
    # Run first task to spawn worker
    pid1 = get_worker_pid(0).result()
    
    # Wait for reaper to kill it (timeout is 1s, check interval is 2s, so 3.5s is safe)
    import time
    time.sleep(3.5)
    
    # Run second task
    pid2 = get_worker_pid(0).result()
    
    # PIDs should be different as the first worker was reaped and a new one spawned
    assert pid1 != pid2


def test_isolated_min_workers():
    import os
    import subprocess
    import sys

    # Run a python subprocess to test this in isolation
    code = """
import os
import time
import sys
sys.path.insert(0, os.path.abspath("python"))
sys.path.insert(0, os.path.abspath("."))
from tests.isolated_helper import get_worker_pid

# Run first task to spawn worker
pid1 = get_worker_pid(0).result()

# Wait 3.5s (longer than 1s timeout + 2s reaper interval)
time.sleep(3.5)

# Run second task
pid2 = get_worker_pid(0).result()

# With PYROXIDE_MIN_WORKERS=1, the worker should NOT be reaped, so PIDs should be identical
print(f"PIDS: {pid1} {pid2}")
assert pid1 == pid2, f"Worker was reaped even though PYROXIDE_MIN_WORKERS=1 (pid1={pid1}, pid2={pid2})"
"""
    env = os.environ.copy()
    env["PYROXIDE_MIN_WORKERS"] = "1"
    env["PYROXIDE_IDLE_TIMEOUT_SEC"] = "1"
    # Ensure sys.path and PYTHONPATH are clean or propagated
    env["PYTHONPATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "python")) + ":" + env.get("PYTHONPATH", "")
    
    res = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    assert res.returncode == 0, f"Subprocess failed:\nstdout: {res.stdout}\nstderr: {res.stderr}"


def test_isolated_dylib_custom_symbols():
    """Verifies that load_dylib works with custom symbol lookup inside isolated worker processes."""
    from pyroxide import compile_c, load_dylib

    C_SRC = """
    #include <stdint.h>
    #include <stdlib.h>
    
    uint8_t* my_shift_fn(const uint8_t* ptr, size_t len, size_t* out_len) {
        uint8_t* res = (uint8_t*)malloc(len);
        for (size_t i = 0; i < len; i++) {
            res[i] = ptr[i] + 2; // Caesar cipher +2
        }
        *out_len = len;
        return res;
    }
    
    void pyroxide_plugin_free(uint8_t* ptr, size_t len) {
        free(ptr);
    }
    """
    compile_c("caesar_custom_iso", C_SRC)
    proxy = load_dylib("caesar_custom_iso", isolated=True)

    handle = proxy.my_shift_fn(b"abc")
    assert handle.result() == b"cde"


def test_isolated_dylib_ffi():
    """Verifies that load_dylib works with signatures inside isolated worker processes."""
    from pyroxide import compile_c, load_dylib

    C_SRC = """
    #include <stdint.h>
    #include <stddef.h>
    
    int32_t my_mul_fn(int32_t a, int32_t b) {
        return a * b;
    }
    
    void pyroxide_plugin_free(void* ptr, size_t len) {
        // Dummy
    }
    """
    compile_c("mul_ffi_iso", C_SRC)
    proxy = load_dylib(
        "mul_ffi_iso",
        signatures={"my_mul_fn": {"args": ["i32", "i32"], "ret": "i32"}},
        isolated=True,
    )

    handle = proxy.my_mul_fn(6, 7)
    assert handle.result() == 42






