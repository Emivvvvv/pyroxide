from types import SimpleNamespace
from unittest.mock import patch

import pyroxide._native_compile as native_compile
import pyroxide.wasm as wasm
import pytest
from pyroxide.plugins import compile_c, compile_rust, compile_zig


def test_cargo_missing():
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError) as exc_info:
            compile_rust("test_missing", "fn main() {}")
        assert (
            "Required compiler system binary 'cargo' is not found on your PATH"
            in str(exc_info.value)
        )


def test_cc_missing():
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError) as exc_info:
            compile_c("test_missing", "int main() {}")
        assert "Required compiler system binary" in str(exc_info.value)


def test_zig_missing():
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError) as exc_info:
            compile_zig("test_missing", "pub fn main() void {}")
        assert "Required compiler system binary 'zig' is not found on your PATH" in str(
            exc_info.value
        )


def test_cross_process_lock_failure_releases_thread_lock():
    with (
        patch("pyroxide._native_compile._verify_compiler"),
        patch.object(
            native_compile.CrossProcessLock,
            "acquire",
            side_effect=TimeoutError("lock unavailable"),
        ),
    ):
        with pytest.raises(TimeoutError, match="lock unavailable"):
            compile_c("lock_failure", "void ignored(void) {}")

    assert native_compile._compile_lock.acquire(timeout=0.1)
    native_compile._compile_lock.release()


def test_cache_directory_can_be_configured(tmp_path, monkeypatch):
    cache_dir = tmp_path / "company-cache"
    monkeypatch.setenv("PYROXIDE_CACHE_DIR", str(cache_dir))
    assert native_compile._cache_dir() == str(cache_dir)


def test_published_library_replaces_destination_atomically(tmp_path):
    compiled = tmp_path / "compiled.so"
    compiled.write_bytes(b"new")
    destination = tmp_path / "cache"
    destination.mkdir()
    (destination / "plugin.so").write_bytes(b"old")

    result = native_compile._publish_library(
        str(compiled), str(destination), "plugin.so"
    )

    assert result == str(destination / "plugin.so")
    assert (destination / "plugin.so").read_bytes() == b"new"
    assert not list(destination.glob(".plugin.so.*"))


def test_wasm_compiler_commands_use_configured_timeout(monkeypatch):
    monkeypatch.setenv("PYROXIDE_COMPILER_TIMEOUT_SEC", "17")
    failed = SimpleNamespace(returncode=1, stdout="", stderr="compile failed")

    with (
        patch("pyroxide.wasm._verify_compiler"),
        patch("pyroxide.wasm.subprocess.run", return_value=failed) as run,
        pytest.raises(RuntimeError, match="C to WASM compilation failed"),
    ):
        wasm.compile_c_wasm("timeout_test", "int run(void) { return 0; }")

    assert run.call_count == 2
    assert all(call.kwargs["timeout"] == 17 for call in run.call_args_list)


def test_wasm_lock_failure_releases_thread_lock():
    with (
        patch("pyroxide.wasm._verify_compiler"),
        patch.object(
            native_compile.CrossProcessLock,
            "acquire",
            side_effect=TimeoutError("lock unavailable"),
        ),
    ):
        with pytest.raises(TimeoutError, match="lock unavailable"):
            wasm.compile_c_wasm("lock_failure", "int run(void) { return 0; }")

    assert native_compile._compile_lock.acquire(timeout=0.1)
    native_compile._compile_lock.release()
