import inspect
import pytest
import pyroxide
from pyroxide import (
    CompilerNotFoundError,
    ForkSafetyError,
    TaskGroup,
    TaskHandle,
    compile_rust,
    group,
    load_dylib,
    load_wasm,
    register_wasm,
    shutdown,
    task,
    wasm_task,
)


def test_public_all_exports():
    expected_all = [
        "task",
        "TaskHandle",
        "register_wasm",
        "register_wasm_wat",
        "wasm_task",
        "load_wasm",
        "compile_wasm",
        "compile_wat_wasm",
        "compile_c_wasm",
        "compile_rust_wasm",
        "compile_zig_wasm",
        "compile_rust",
        "dylib_task",
        "load_dylib",
        "unregister_dylib",
        "compile_c",
        "compile_zig",
        "group",
        "TaskGroup",
        "shutdown",
        "ForkSafetyError",
        "generate_stubs",
        "set_wasm_limits",
        "set_queue_timeout",
        "scoped",
        "is_free_threaded",
        "stats",
        "config",
        "CompilerNotFoundError",
    ]
    assert sorted(pyroxide.__all__) == sorted(expected_all)


def test_version_string():
    assert pyroxide.__version__ == "1.0.0rc1"


def test_essential_imports_exposed():
    assert task is pyroxide.task
    assert TaskHandle is pyroxide.TaskHandle
    assert wasm_task is pyroxide.wasm_task
    assert load_wasm is pyroxide.load_wasm
    assert load_dylib is pyroxide.load_dylib
    assert compile_rust is pyroxide.compile_rust
    assert group is pyroxide.group
    assert shutdown is pyroxide.shutdown


def test_function_signatures():
    sig_shutdown = inspect.signature(pyroxide.shutdown)
    assert list(sig_shutdown.parameters.keys()) == ["wait", "cancel_pending"]
    assert sig_shutdown.parameters["wait"].default is True
    assert sig_shutdown.parameters["cancel_pending"].default is False

    sig_set_wasm_limits = inspect.signature(pyroxide.set_wasm_limits)
    assert list(sig_set_wasm_limits.parameters.keys()) == ["memory_limit_bytes", "timeout_ms"]
    assert sig_set_wasm_limits.parameters["memory_limit_bytes"].default is None
    assert sig_set_wasm_limits.parameters["timeout_ms"].default is None

    sig_set_queue_timeout = inspect.signature(pyroxide.set_queue_timeout)
    assert list(sig_set_queue_timeout.parameters.keys()) == ["timeout_ms"]


def test_exception_classes():
    assert issubclass(ForkSafetyError, RuntimeError)
    assert issubclass(CompilerNotFoundError, RuntimeError)


def sample_func_api(x):
    return x * 2

def test_task_status_strings():
    decorated = task(sample_func_api)
    handle = decorated(5)
    assert handle.status in {"Pending", "Running", "Completed"}
    res = handle.result(timeout_sec=5.0)
    assert res == 10


def test_decorated_function_metadata():
    def dummy_func(a):
        """Dummy docstring."""
        return a

    decorated = task(dummy_func)
    assert decorated.__name__ == "dummy_func"
    assert decorated.__doc__ == "Dummy docstring."
    assert decorated.__wrapped__ is dummy_func
    assert hasattr(decorated, "__module__")
