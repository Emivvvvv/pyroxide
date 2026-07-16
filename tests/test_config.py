import pytest
import time
import threading
import pyroxide
from pyroxide import register_wasm_wat, wasm_task, TaskHandle

# WebAssembly module that runs an infinite loop
WAT_INFINITE_LOOP = """
(module
  (memory (export "memory") 1)
  (func (export "run") (param i32 i32) (result i64)
    (loop
      br 0
    )
    i64.const 0
  )
  (func (export "alloc") (param i32) (result i32)
    i32.const 0
  )
  (func (export "dealloc") (param i32) (param i32)
  )
)
"""


def test_config_global_and_scoped_wasm_timeout():
    register_wasm_wat("infinite_loop_mod", WAT_INFINITE_LOOP)

    @wasm_task("infinite_loop_mod")
    def run_loop(payload: str) -> str:
        pass

    # 1. Global config timeout is 1000ms by default.
    # Let's verify scoped override of 50ms fails quickly.
    with pyroxide.config.scoped(wasm_timeout_ms=50):
        t0 = time.time()
        handle = run_loop("start")
        with pytest.raises(Exception) as exc_info:
            handle.result()
        duration = (time.time() - t0) * 1000
        # Should fail fast, well under the 1000ms global default
        assert duration < 500
        assert "wasm execution failed" in str(exc_info.value).lower()

    # 2. Test global config setter.
    # Set global wasm timeout to 100ms.
    pyroxide.config.set_wasm_limits(timeout_ms=100)
    t0 = time.time()
    handle2 = run_loop("start")
    with pytest.raises(Exception) as exc_info:
        handle2.result()
    duration2 = (time.time() - t0) * 1000
    assert duration2 < 600
    assert "wasm execution failed" in str(exc_info.value).lower()

    # Restore default global timeout
    pyroxide.config.set_wasm_limits(timeout_ms=1000)


def test_config_thread_safety():
    # Test that scoped overrides on one thread don't affect another thread
    results = {}

    def worker_with_override():
        with pyroxide.config.scoped(wasm_timeout_ms=50):
            # Wait a moment to let the other thread dispatch
            time.sleep(0.1)
            from pyroxide.config import _local

            results["override_thread_val"] = getattr(_local, "wasm_timeout_ms", None)

    def worker_without_override():
        # Sleep to let override enter
        time.sleep(0.05)
        from pyroxide.config import _local

        results["normal_thread_val"] = getattr(_local, "wasm_timeout_ms", None)

    t1 = threading.Thread(target=worker_with_override)
    t2 = threading.Thread(target=worker_without_override)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["override_thread_val"] == 50
    assert results["normal_thread_val"] is None
