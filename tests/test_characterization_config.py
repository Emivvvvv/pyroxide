import pytest
from pyroxide.config import (
    _get_scoped_queue_timeout_ms,
    _get_scoped_wasm_timeout_ms,
    _validate_environment,
    scoped,
    set_queue_timeout,
    set_wasm_limits,
)


def test_env_validation_defaults():
    # Calling without throwing means current env is valid
    _validate_environment()


def test_env_validation_invalid_integers(monkeypatch):
    monkeypatch.setenv("PYROXIDE_WORKERS", "not_an_int")
    with pytest.raises(ValueError, match="PYROXIDE_WORKERS must be an integer"):
        _validate_environment()


def test_env_validation_below_minimum(monkeypatch):
    monkeypatch.setenv("PYROXIDE_WORKERS", "0")
    with pytest.raises(ValueError, match="PYROXIDE_WORKERS must be at least 1"):
        _validate_environment()


def test_env_validation_compiler_timeout(monkeypatch):
    monkeypatch.setenv("PYROXIDE_COMPILER_TIMEOUT_SEC", "-5.0")
    with pytest.raises(ValueError, match="PYROXIDE_COMPILER_TIMEOUT_SEC must be a positive number"):
        _validate_environment()

    monkeypatch.setenv("PYROXIDE_COMPILER_TIMEOUT_SEC", "invalid")
    with pytest.raises(ValueError, match="PYROXIDE_COMPILER_TIMEOUT_SEC must be a positive number"):
        _validate_environment()


def test_env_validation_wasm_memory_limit_exceeded(monkeypatch):
    monkeypatch.setenv("PYROXIDE_WASM_MEMORY_LIMIT_BYTES", str(2**31 + 100))
    with pytest.raises(ValueError, match="PYROXIDE_WASM_MEMORY_LIMIT_BYTES must not exceed"):
        _validate_environment()


def test_env_validation_min_workers_exceeds_max(monkeypatch):
    monkeypatch.setenv("PYROXIDE_MAX_PROCESSES", "2")
    monkeypatch.setenv("PYROXIDE_MIN_WORKERS", "5")
    with pytest.raises(ValueError, match="PYROXIDE_MIN_WORKERS cannot exceed PYROXIDE_MAX_PROCESSES"):
        _validate_environment()


def test_set_wasm_limits_validation():
    with pytest.raises(ValueError, match="must be a positive integer"):
        set_wasm_limits(memory_limit_bytes=-100)

    with pytest.raises(ValueError, match="must not exceed"):
        set_wasm_limits(memory_limit_bytes=2**32)

    with pytest.raises(ValueError, match="must be a positive integer"):
        set_wasm_limits(timeout_ms=0)


def test_set_queue_timeout_validation():
    with pytest.raises(ValueError, match="must be a non-negative integer"):
        set_queue_timeout(-5)


def test_scoped_context_nesting_and_restoration():
    assert _get_scoped_wasm_timeout_ms() is None
    assert _get_scoped_queue_timeout_ms() is None

    with scoped(wasm_timeout_ms=1000, queue_timeout_ms=500):
        assert _get_scoped_wasm_timeout_ms() == 1000
        assert _get_scoped_queue_timeout_ms() == 500

        with scoped(wasm_timeout_ms=2000):
            assert _get_scoped_wasm_timeout_ms() == 2000
            assert _get_scoped_queue_timeout_ms() == 500

        assert _get_scoped_wasm_timeout_ms() == 1000
        assert _get_scoped_queue_timeout_ms() == 500

    assert _get_scoped_wasm_timeout_ms() is None
    assert _get_scoped_queue_timeout_ms() is None


def test_scoped_context_restoration_on_exception():
    with pytest.raises(RuntimeError):
        with scoped(wasm_timeout_ms=1234):
            assert _get_scoped_wasm_timeout_ms() == 1234
            raise RuntimeError("Test error")

    assert _get_scoped_wasm_timeout_ms() is None
