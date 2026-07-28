import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_child(code: str, **overrides: str) -> subprocess.CompletedProcess[str]:
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
        timeout=20,
    )


def test_batch_rejection_leaves_no_inaccessible_tasks():
    result = run_child(
        """
        import threading
        import pyroxide
        from pyroxide import task

        started = threading.Event()
        release = threading.Event()

        @task
        def blocker(value):
            started.set()
            release.wait()
            return value

        @task
        def echo(value):
            return value

        blocker_handle = blocker(1)
        assert started.wait(5)
        before = pyroxide.stats()

        try:
            echo.batch(list(range(10_001)))
        except BufferError:
            pass
        else:
            raise AssertionError("oversized batch was accepted")

        after = pyroxide.stats()
        assert after["active_tasks"] == before["active_tasks"], (before, after)
        assert after["submitted_tasks"] == before["submitted_tasks"], (before, after)

        release.set()
        assert blocker_handle.result() == 1
        """,
        PYROXIDE_WORKERS="1",
    )
    assert result.returncode == 0, result.stderr


def test_batch_rejection_is_atomic_when_queue_is_partly_full():
    result = run_child(
        """
        import threading
        import pyroxide
        from pyroxide import task

        started = threading.Event()
        release = threading.Event()

        @task
        def blocker(value):
            started.set()
            release.wait()
            return value

        @task
        def echo(value):
            return value

        blocker_handle = blocker(1)
        assert started.wait(5)
        queued_handles = echo.batch(list(range(9_999)))
        before = pyroxide.stats()

        try:
            echo.batch([10_000, 10_001])
        except BufferError:
            pass
        else:
            raise AssertionError("partially fitting batch was accepted")

        after = pyroxide.stats()
        assert after["active_tasks"] == before["active_tasks"], (before, after)
        assert after["submitted_tasks"] == before["submitted_tasks"], (before, after)

        for handle in queued_handles:
            handle.cancel()
        release.set()
        assert blocker_handle.result() == 1
        """,
        PYROXIDE_WORKERS="1",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("isolated", [False, True])
def test_wasm_batch_rejection_never_admits_a_prefix(isolated, tmp_path):
    result = run_child(
        f"""
        import threading
        import time
        from pathlib import Path

        import pyroxide
        from pyroxide import load_wasm, register_wasm
        from tests.test_wasm import WASM_BYTES

        isolated = {isolated!r}
        release = threading.Event()

        if isolated:
            from tests.isolated_helper import report_pid_then_sleep

            pid_path = Path({str(tmp_path / "wasm-blocker.pid")!r})
            blocker_handle = report_pid_then_sleep((str(pid_path), 10))
            deadline = time.monotonic() + 5
            while not pid_path.exists():
                assert time.monotonic() < deadline
                time.sleep(0.001)
        else:
            from pyroxide import task

            started = threading.Event()

            @task
            def controlled_blocker(value):
                started.set()
                release.wait()
                return value

            blocker_handle = controlled_blocker(1)
            assert started.wait(5)

        register_wasm("atomic_wasm_batch", WASM_BYTES)
        proxy = load_wasm("atomic_wasm_batch", isolated=isolated)
        before = pyroxide.stats()

        try:
            proxy.run.batch(["first", "second"])
        except BufferError:
            pass
        else:
            raise AssertionError("partially fitting WASM batch was accepted")

        after_rejection = pyroxide.stats()
        if isolated:
            assert blocker_handle.cancel() is True
            try:
                blocker_handle.result()
            except RuntimeError as error:
                assert "cancelled" in str(error).lower()
            else:
                raise AssertionError("isolated blocker was not cancelled")
        else:
            release.set()
            assert blocker_handle.result() == 1

        pyroxide.shutdown(wait=True)
        after_drain = pyroxide.stats()

        assert after_rejection["submitted_tasks"] == before["submitted_tasks"], (
            before,
            after_rejection,
        )
        assert after_drain["submitted_tasks"] == before["submitted_tasks"], (
            before,
            after_drain,
        )
        expected_completed = before["completed_tasks"] + (0 if isolated else 1)
        assert after_drain["completed_tasks"] == expected_completed, after_drain
        """,
        PYROXIDE_WORKERS="1",
        PYROXIDE_MAX_PROCESSES="1",
        PYROXIDE_MAX_TASKS_PER_WORKER="0",
        PYROXIDE_QUEUE_CAPACITY="1",
        PYROXIDE_QUEUE_TIMEOUT_MS="0",
        PYROXIDE_IDLE_TIMEOUT_SEC="60",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize("batch_kind", ["decorator", "proxy", "ffi"])
def test_native_batch_rejection_never_executes_a_prefix(
    isolated, batch_kind, tmp_path
):
    marker_path = tmp_path / f"{batch_kind}-{isolated}.marker"
    result = run_child(
        f"""
        import os
        import threading
        import time
        from pathlib import Path

        import pyroxide
        from pyroxide import compile_c, dylib_task, load_dylib

        isolated = {isolated!r}
        batch_kind = {batch_kind!r}
        marker_path = Path(os.environ["PYROXIDE_BATCH_MARKER"])
        release = threading.Event()

        if isolated:
            from tests.isolated_helper import report_pid_then_sleep

            pid_path = marker_path.with_suffix(".pid")
            blocker_handle = report_pid_then_sleep((str(pid_path), 10))
            deadline = time.monotonic() + 5
            while not pid_path.exists():
                assert time.monotonic() < deadline
                time.sleep(0.001)
        else:
            from pyroxide import task

            started = threading.Event()

            @task
            def controlled_blocker(value):
                started.set()
                release.wait()
                return value

            blocker_handle = controlled_blocker(1)
            assert started.wait(5)

        source = r'''
        #include <stdint.h>
        #include <stdio.h>
        #include <stdlib.h>
        #include <string.h>

        static void write_marker(void) {{
            const char* marker = getenv("PYROXIDE_BATCH_MARKER");
            if (marker != NULL) {{
                FILE* file = fopen(marker, "ab");
                if (file != NULL) {{
                    fputc('x', file);
                    fclose(file);
                }}
            }}
        }}

        uint8_t* pyroxide_plugin_run(
            const uint8_t* ptr,
            size_t len,
            size_t* out_len
        ) {{
            write_marker();
            uint8_t* result = (uint8_t*)malloc(len);
            memcpy(result, ptr, len);
            *out_len = len;
            return result;
        }}

        void pyroxide_plugin_free(uint8_t* ptr, size_t len) {{
            free(ptr);
        }}

        int32_t marker_add(int32_t value) {{
            write_marker();
            return value + 1;
        }}
        '''
        compile_c("atomic_native_batch", source)

        if batch_kind == "decorator":
            @dylib_task("atomic_native_batch", isolated=isolated)
            def native_call(payload):
                pass

            submit_batch = lambda: native_call.batch([b"first", b"second"])
        elif batch_kind == "proxy":
            proxy = load_dylib("atomic_native_batch", isolated=isolated)
            submit_batch = lambda: proxy.pyroxide_plugin_run.batch(
                [b"first", b"second"]
            )
        else:
            proxy = load_dylib(
                "atomic_native_batch",
                signatures={{
                    "marker_add": {{"args": ["i32"], "ret": "i32"}},
                }},
                isolated=isolated,
            )
            submit_batch = lambda: proxy.marker_add.batch([(1,), (2,)])

        before = pyroxide.stats()
        try:
            submit_batch()
        except BufferError:
            pass
        else:
            raise AssertionError("partially fitting native batch was accepted")

        after_rejection = pyroxide.stats()
        if isolated:
            assert blocker_handle.cancel() is True
            try:
                blocker_handle.result()
            except RuntimeError as error:
                assert "cancelled" in str(error).lower()
            else:
                raise AssertionError("isolated blocker was not cancelled")
        else:
            release.set()
            assert blocker_handle.result() == 1

        pyroxide.shutdown(wait=True)
        after_drain = pyroxide.stats()

        assert after_rejection["submitted_tasks"] == before["submitted_tasks"], (
            before,
            after_rejection,
        )
        assert after_drain["submitted_tasks"] == before["submitted_tasks"], (
            before,
            after_drain,
        )
        assert not marker_path.exists(), marker_path.read_bytes()
        """,
        PYROXIDE_WORKERS="1",
        PYROXIDE_MAX_PROCESSES="1",
        PYROXIDE_MAX_TASKS_PER_WORKER="0",
        PYROXIDE_QUEUE_CAPACITY="1",
        PYROXIDE_QUEUE_TIMEOUT_MS="0",
        PYROXIDE_IDLE_TIMEOUT_SEC="60",
        PYROXIDE_BATCH_MARKER=str(marker_path),
        PYROXIDE_CACHE_DIR=str(tmp_path / "cache"),
    )
    assert result.returncode == 0, result.stderr


def test_rejected_ffi_batch_does_not_serialize_payloads_before_admission():
    result = run_child(
        """
        import threading

        from pyroxide import task
        from pyroxide.plugins import DylibProxy

        started = threading.Event()
        release = threading.Event()

        @task
        def blocker(value):
            started.set()
            release.wait()
            return value

        class SerializationProbe:
            calls = 0

            def __index__(self):
                type(self).calls += 1
                raise AssertionError("FFI payload serialized before batch admission")

        blocker_handle = blocker(1)
        assert started.wait(5)

        proxy = DylibProxy(
            "capacity_rejection_needs_no_library",
            signatures={"probe": {"args": ["i32"], "ret": "i32"}},
        )
        try:
            proxy.probe.batch([(SerializationProbe(),), (SerializationProbe(),)])
        except BufferError:
            pass
        else:
            raise AssertionError("oversized FFI batch was accepted")

        assert SerializationProbe.calls == 0
        release.set()
        assert blocker_handle.result() == 1
        """,
        PYROXIDE_WORKERS="1",
        PYROXIDE_QUEUE_CAPACITY="1",
        PYROXIDE_QUEUE_TIMEOUT_MS="0",
    )
    assert result.returncode == 0, result.stderr


def test_ffi_batch_builder_failure_releases_reserved_capacity():
    result = run_child(
        """
        from pyroxide import task
        from pyroxide.plugins import DylibProxy

        @task
        def echo(value):
            return value

        class InvalidArgument:
            def __index__(self):
                raise AssertionError("expected serialization failure")

        proxy = DylibProxy(
            "builder_failure_needs_no_library",
            signatures={"probe": {"args": ["i32"], "ret": "i32"}},
        )
        try:
            proxy.probe.batch([(InvalidArgument(),)])
        except AssertionError as error:
            assert "expected serialization failure" in str(error)
        else:
            raise AssertionError("invalid FFI argument was accepted")

        assert echo(17).result() == 17
        """,
        PYROXIDE_WORKERS="1",
        PYROXIDE_QUEUE_CAPACITY="1",
        PYROXIDE_QUEUE_TIMEOUT_MS="0",
    )
    assert result.returncode == 0, result.stderr


def test_queue_capacity_is_configurable():
    result = run_child(
        """
        import threading
        import pyroxide
        from pyroxide import task

        started = threading.Event()
        release = threading.Event()

        @task
        def blocker(value):
            started.set()
            release.wait()
            return value

        first = blocker(1)
        assert started.wait(5)
        queued = blocker(2)

        try:
            blocker(3)
        except BufferError:
            pass
        else:
            raise AssertionError("configured queue capacity was ignored")

        current = pyroxide.stats()
        assert current["queue_capacity"] == 1
        assert current["queued_tasks"] == 1
        assert current["running_tasks"] == 1
        assert current["rejected_tasks"] == 1

        queued.cancel()
        release.set()
        assert first.result() == 1
        """,
        PYROXIDE_WORKERS="1",
        PYROXIDE_QUEUE_CAPACITY="1",
    )
    assert result.returncode == 0, result.stderr


def test_zero_workers_is_rejected_during_import():
    result = run_child(
        """
        try:
            import pyroxide
        except ValueError as error:
            assert "PYROXIDE_WORKERS" in str(error)
        else:
            raise AssertionError("zero workers were accepted")
        """,
        PYROXIDE_WORKERS="0",
    )
    assert result.returncode == 0, result.stderr


def test_invalid_compiler_timeout_is_rejected_during_import():
    result = run_child(
        """
        try:
            import pyroxide
        except ValueError as error:
            assert "PYROXIDE_COMPILER_TIMEOUT_SEC" in str(error)
        else:
            raise AssertionError("invalid compiler timeout was accepted")
        """,
        PYROXIDE_COMPILER_TIMEOUT_SEC="0",
    )
    assert result.returncode == 0, result.stderr
