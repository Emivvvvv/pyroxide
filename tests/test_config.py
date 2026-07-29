import threading
import time

import pyroxide
import pytest
from pyroxide import register_wasm_wat, wasm_task

from tests.test_admission import run_child

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
        # Should fail fast, well under the 5000ms global default
        assert duration < 1500
        assert "wasm execution failed" in str(exc_info.value).lower()

    # 2. Test global config setter.
    # Set global wasm timeout to 100ms.
    pyroxide.config.set_wasm_limits(timeout_ms=100)
    t0 = time.time()
    handle2 = run_loop("start")
    with pytest.raises(Exception) as exc_info:
        handle2.result()
    duration2 = (time.time() - t0) * 1000
    assert duration2 < 2500
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
            from pyroxide.config import _get_scoped_wasm_timeout_ms

            results["override_thread_val"] = _get_scoped_wasm_timeout_ms()

    def worker_without_override():
        # Sleep to let override enter
        time.sleep(0.05)
        from pyroxide.config import _get_scoped_wasm_timeout_ms

        results["normal_thread_val"] = _get_scoped_wasm_timeout_ms()

    t1 = threading.Thread(target=worker_with_override)
    t2 = threading.Thread(target=worker_without_override)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["override_thread_val"] == 50
    assert results["normal_thread_val"] is None


def test_config_asyncio_task_safety():
    import asyncio

    from pyroxide.config import _get_scoped_wasm_timeout_ms, scoped

    async def main():
        event_a = asyncio.Event()
        event_b = asyncio.Event()
        results = {}

        async def coroutine_a():
            with scoped(wasm_timeout_ms=123):
                event_a.set()
                await event_b.wait()
                # Interleaved pause while coroutine_b runs on the same thread
                await asyncio.sleep(0.01)
                results["a"] = _get_scoped_wasm_timeout_ms()

        async def coroutine_b():
            with scoped(wasm_timeout_ms=456):
                event_b.set()
                await event_a.wait()
                await asyncio.sleep(0.01)
                results["b"] = _get_scoped_wasm_timeout_ms()

        await asyncio.gather(coroutine_a(), coroutine_b())
        assert results["a"] == 123
        assert results["b"] == 456

    asyncio.run(main())


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"memory_limit_bytes": 0}, "memory_limit_bytes must be a positive integer"),
        ({"timeout_ms": 0}, "timeout_ms must be a positive integer"),
        ({"memory_limit_bytes": True}, "memory_limit_bytes must be a positive integer"),
    ],
)
def test_global_wasm_limits_reject_invalid_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        pyroxide.config.set_wasm_limits(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"wasm_timeout_ms": 0},
        {"wasm_memory_limit_bytes": -1},
        {"queue_timeout_ms": -1},
    ],
)
def test_scoped_limits_reject_invalid_values(kwargs):
    with pytest.raises(ValueError):
        with pyroxide.config.scoped(**kwargs):
            pass


def test_queue_timeout_accepts_zero_but_rejects_negative():
    pyroxide.config.set_queue_timeout(0)
    with pytest.raises(ValueError, match="timeout_ms must be a non-negative integer"):
        pyroxide.config.set_queue_timeout(-1)
    pyroxide.config.set_queue_timeout(1000)


def test_wasm_timeout_environment_variable_is_effective():
    result = run_child(
        '''
        import time
        from pyroxide import register_wasm_wat, wasm_task

        wat = """
        (module
          (memory (export "memory") 1)
          (func (export "run") (param i32 i32) (result i64)
            (loop br 0)
            i64.const 0)
          (func (export "alloc") (param i32) (result i32) i32.const 0)
          (func (export "dealloc") (param i32) (param i32)))
        """
        register_wasm_wat("env_timeout", wat)

        @wasm_task("env_timeout")
        def run(payload):
            pass

        started = time.monotonic()
        try:
            run(b"x").result()
        except RuntimeError:
            pass
        else:
            raise AssertionError("infinite WASM task completed")
        assert time.monotonic() - started < 0.5
        ''',
        PYROXIDE_WASM_TIMEOUT_MS="50",
    )
    assert result.returncode == 0, result.stderr
