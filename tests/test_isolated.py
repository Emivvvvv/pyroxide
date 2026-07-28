import concurrent.futures
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pyroxide import compile_c, dylib_task, register_wasm, wasm_task

from tests.isolated_helper import (
    crash_task,
    echo_large_payload,
    functional_square_isolated,
    square_isolated,
)

ROOT = Path(__file__).resolve().parents[1]


def run_isolated_child(
    code: str, **overrides: str
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(overrides)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "python"), str(ROOT), env.get("PYTHONPATH", "")]
    )
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# 1. Test basic Python isolated execution
def test_isolated_python_task():
    handle = square_isolated(9)
    assert handle.result() == 81


def test_functional_style_isolated_python_task():
    """task(func, isolated=True) must keep the original function picklable."""
    assert functional_square_isolated(11).result() == 121


# 2. Test crash safety (Process Exit)
def test_isolated_crash_safety():
    handle = crash_task(0)
    with pytest.raises(RuntimeError) as exc_info:
        handle.result()
    err_msg = str(exc_info.value).lower()
    assert (
        "crashed" in err_msg
        or "eof" in err_msg
        or "broken pipe" in err_msg
        or "connection reset" in err_msg
    )


# 3. Test post-crash pool recovery
def test_isolated_pool_recovery():
    # Crash the worker first
    handle1 = crash_task(0)
    with pytest.raises(RuntimeError):
        handle1.result()

    # The pool should immediately heal and spawn a new worker for the next task
    handle2 = square_isolated(12)
    assert handle2.result() == 144


def test_isolated_coordinator_panic_finishes_task():
    import pyroxide

    handle = square_isolated("TRIGGER_ISOLATED_PANIC")
    with pytest.raises(RuntimeError, match="panicked"):
        handle.result(timeout_sec=2)
    assert pyroxide.stats()["running_tasks"] == 0


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


def test_isolated_process_count_is_bounded():
    import os
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYROXIDE_WORKERS"] = "8"
    env["PYROXIDE_MAX_PROCESSES"] = "2"
    env["PYROXIDE_MAX_TASKS_PER_WORKER"] = "100"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root / "python"), str(root), env.get("PYTHONPATH", "")]
    )
    code = textwrap.dedent(
        """
        from tests.isolated_helper import delayed_worker_pid

        handles = [delayed_worker_pid(0.4) for _ in range(8)]
        pids = {handle.result() for handle in handles}
        assert len(pids) <= 2, pids
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_isolated_worker_recycles_after_exact_task_limit():
    result = run_isolated_child(
        """
        import pyroxide
        from tests.isolated_helper import get_worker_pid

        first_pid = get_worker_pid(None).result()
        for _ in range(99):
            assert get_worker_pid(None).result() == first_pid
        replacement_pid = get_worker_pid(None).result()
        assert replacement_pid != first_pid
        pyroxide.shutdown(wait=True)
        stats = pyroxide.stats()
        assert stats["queued_tasks"] == 0
        assert stats["running_tasks"] == 0
        """,
        PYROXIDE_WORKERS="1",
        PYROXIDE_MAX_PROCESSES="1",
        PYROXIDE_MAX_TASKS_PER_WORKER="100",
        PYROXIDE_IDLE_TIMEOUT_SEC="60",
    )
    assert result.returncode == 0, result.stderr


# 5. Test WASM isolated execution
def test_isolated_wasm_task():
    from pyroxide import load_wasm

    from tests.test_wasm import WASM_BYTES

    register_wasm("rot13_isolated", WASM_BYTES)

    @wasm_task("rot13_isolated", "run", isolated=True)
    def rot13_cipher(payload: str) -> str:
        pass

    handle = rot13_cipher("Hello Isolated WASM!")
    assert handle.result() == "Uryyb Vfbyngrq JNFZ!"
    proxy = load_wasm("rot13_isolated", isolated=True)
    handles = proxy.run.batch(["Batch One", "Batch Two"])
    assert [handle.result() for handle in handles] == ["Ongpu Bar", "Ongpu Gjb"]


def test_warm_isolated_worker_refreshes_replaced_wasm():
    result = run_isolated_child(
        """
        import pyroxide
        from pyroxide import load_wasm, register_wasm_wat

        def constant_wat(value):
            pointer = 32
            packed = (pointer << 32) | len(value)
            return f'''
            (module
              (memory (export "memory") 1)
              (data (i32.const {pointer}) "{value}")
              (func (export "run") (param i32 i32) (result i64)
                i64.const {packed}
              )
              (func (export "alloc") (param i32) (result i32) i32.const 0)
              (func (export "dealloc") (param i32 i32))
            )
            '''

        register_wasm_wat("replace_wasm", constant_wat("old"))
        proxy = load_wasm("replace_wasm", isolated=True)
        assert proxy.run("payload").result() == "old"

        register_wasm_wat("replace_wasm", constant_wat("new"))
        replaced = proxy.run("payload").result()
        pyroxide.shutdown(wait=True)

        assert replaced == "new"
        """,
        PYROXIDE_WORKERS="1",
        PYROXIDE_MAX_PROCESSES="1",
        PYROXIDE_MAX_TASKS_PER_WORKER="0",
        PYROXIDE_IDLE_TIMEOUT_SEC="60",
    )
    assert result.returncode == 0, result.stderr


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
    handles = caesar_cipher.batch([b"abc", b"xyz"])
    assert [handle.result() for handle in handles] == [b"bcd", b"yz{"]


def test_warm_isolated_worker_refreshes_replaced_dylib(tmp_path):
    result = run_isolated_child(
        """
        import pyroxide
        from pyroxide import compile_c, load_dylib
        from pyroxide._pyroxide import register_dylib

        def shift_source(amount):
            return f'''
            #include <stdint.h>
            #include <stdlib.h>

            uint8_t* pyroxide_plugin_run(
                const uint8_t* ptr,
                size_t len,
                size_t* out_len
            ) {{
                uint8_t* result = (uint8_t*)malloc(len);
                for (size_t index = 0; index < len; index++) {{
                    result[index] = ptr[index] + {amount};
                }}
                *out_len = len;
                return result;
            }}

            void pyroxide_plugin_free(uint8_t* ptr, size_t len) {{
                free(ptr);
            }}
            '''

        first_path = compile_c("replace_dylib_first", shift_source(1))
        second_path = compile_c("replace_dylib_second", shift_source(2))
        register_dylib("replace_dylib", first_path)

        proxy = load_dylib("replace_dylib", isolated=True)
        assert proxy.pyroxide_plugin_run(b"abc").result() == b"bcd"

        register_dylib("replace_dylib", second_path)
        replaced = proxy.pyroxide_plugin_run(b"abc").result()
        pyroxide.shutdown(wait=True)

        assert replaced == b"cde"
        """,
        PYROXIDE_WORKERS="1",
        PYROXIDE_MAX_PROCESSES="1",
        PYROXIDE_MAX_TASKS_PER_WORKER="0",
        PYROXIDE_IDLE_TIMEOUT_SEC="60",
        PYROXIDE_CACHE_DIR=str(tmp_path / "cache"),
    )
    assert result.returncode == 0, result.stderr


def test_unregister_blocks_stale_dylib_after_active_worker_returns(tmp_path):
    result = run_isolated_child(
        f"""
        import time
        from pathlib import Path

        import pyroxide
        from pyroxide import compile_c, load_dylib, unregister_dylib
        from tests.isolated_helper import get_worker_pid, report_pid_then_sleep

        source = '''
        #include <stdint.h>
        #include <stdlib.h>

        uint8_t* pyroxide_plugin_run(
            const uint8_t* ptr,
            size_t len,
            size_t* out_len
        ) {{
            uint8_t* result = (uint8_t*)malloc(len);
            for (size_t index = 0; index < len; index++) {{
                result[index] = ptr[index] + 1;
            }}
            *out_len = len;
            return result;
        }}

        void pyroxide_plugin_free(uint8_t* ptr, size_t len) {{
            free(ptr);
        }}
        '''
        compile_c("unregister_active_dylib", source)
        proxy = load_dylib("unregister_active_dylib", isolated=True)
        assert proxy.pyroxide_plugin_run(b"abc").result() == b"bcd"

        pid_path = Path({str(tmp_path / "active-worker.pid")!r})
        active = report_pid_then_sleep((str(pid_path), 0.2))
        deadline = time.monotonic() + 5
        while not pid_path.exists():
            assert time.monotonic() < deadline
            time.sleep(0.001)

        unregister_dylib("unregister_active_dylib")
        active_worker_pid = active.result()
        assert active_worker_pid > 0

        stale_result = None
        stale_error = None
        try:
            stale_result = proxy.pyroxide_plugin_run(b"abc").result()
        except RuntimeError as error:
            stale_error = str(error)

        next_worker_pid = get_worker_pid(None).result()
        pyroxide.shutdown(wait=True)
        assert stale_result is None, stale_result
        assert stale_error is not None and "not found" in stale_error.lower(), stale_error
        assert next_worker_pid == active_worker_pid
        """,
        PYROXIDE_WORKERS="1",
        PYROXIDE_MAX_PROCESSES="1",
        PYROXIDE_MAX_TASKS_PER_WORKER="0",
        PYROXIDE_IDLE_TIMEOUT_SEC="60",
        PYROXIDE_CACHE_DIR=str(tmp_path / "cache"),
    )
    assert result.returncode == 0, result.stderr


def test_isolated_large_payload_shm():
    # 1.5 MB payload
    large_data = "A" * (1024 * 1024 + 100 * 1024)
    handle = echo_large_payload(large_data)
    result = handle.result()
    assert len(result) == len(large_data)
    assert result == large_data


def test_shared_memory_frames_respect_ipc_limit():
    import os
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYROXIDE_MAX_IPC_FRAME_BYTES"] = "1024"
    env["PYROXIDE_SHM_THRESHOLD"] = "512"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root / "python"), str(root), env.get("PYTHONPATH", "")]
    )
    code = textwrap.dedent(
        """
        import pytest
        from tests.isolated_helper import echo_large_payload, make_large_response

        with pytest.raises(RuntimeError, match="exceeds limit"):
            echo_large_payload("x" * 4096).result()
        with pytest.raises(RuntimeError, match="exceeds limit"):
            make_large_response(4096).result()
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_isolated_scale_to_zero():
    import os
    import subprocess
    import sys

    code = """
import os
import time
import sys
sys.path.insert(0, os.path.abspath("python"))
sys.path.insert(0, os.path.abspath("."))
from tests.isolated_helper import get_worker_pid

pid1 = get_worker_pid(0).result()
time.sleep(3.5)
pid2 = get_worker_pid(0).result()
assert pid1 != pid2
print("SUCCESS")
"""
    env = os.environ.copy()
    env["PYROXIDE_IDLE_TIMEOUT_SEC"] = "1"
    env["PYTHONPATH"] = (
        f"{os.path.abspath('python')}:{os.path.abspath('.')}:{env.get('PYTHONPATH', '')}"
    )
    res = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert res.returncode == 0, f"Subprocess failed: {res.stderr}"
    assert "SUCCESS" in res.stdout


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
    env["PYTHONPATH"] = (
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "python"))
        + ":"
        + env.get("PYTHONPATH", "")
    )

    res = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert res.returncode == 0, (
        f"Subprocess failed:\nstdout: {res.stdout}\nstderr: {res.stderr}"
    )


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
    handles = proxy.my_mul_fn.batch([(2, 3), (6, 7)])
    assert [handle.result() for handle in handles] == [6, 42]
