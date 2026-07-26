import pytest
import os
from pyroxide import register_wasm, wasm_task

WASM_PATH = os.path.join(os.path.dirname(__file__), "resources", "rot13.wasm")

with open(WASM_PATH, "rb") as f:
    WASM_BYTES = f.read()


def test_wasm_module_registration():
    # Verify module registers successfully
    register_wasm("rot13_reg", WASM_BYTES)


def test_wasm_task_execution_string():
    register_wasm("rot13_str", WASM_BYTES)

    @wasm_task("rot13_str", "run")
    def rot13_cipher(payload: str) -> str:
        pass

    handle = rot13_cipher("Hello World 123!")
    assert handle.status in ("Pending", "Running", "Completed")
    res = handle.result()
    assert res == "Uryyb Jbeyq 123!"


def test_wasm_task_execution_bytes():
    register_wasm("rot13_bytes", WASM_BYTES)

    @wasm_task("rot13_bytes", "run")
    def rot13_cipher(payload: bytes) -> bytes:
        pass

    handle = rot13_cipher(b"Testing bytes!")
    res = handle.result()
    assert res == b"Grfgvat olgrf!"


def test_wasm_invalid_module_fails():
    with pytest.raises(ValueError) as exc_info:
        register_wasm("bad_mod", b"invalid-wasm-bytecode")
    assert "Failed to compile WASM module" in str(exc_info.value)


def test_wasm_missing_function_fails():
    register_wasm("rot13_missing", WASM_BYTES)

    @wasm_task("rot13_missing", "nonexistent_function")
    def bad_task(payload: str) -> str:
        pass

    handle = bad_task("hello")
    with pytest.raises(RuntimeError) as exc_info:
        handle.result()
    assert "missing export 'nonexistent_function'" in str(exc_info.value)


def test_wasm_parallel_execution():
    register_wasm("rot13_multi", WASM_BYTES)

    @wasm_task("rot13_multi", "run")
    def rot13_cipher(payload: str) -> str:
        pass

    handles = [rot13_cipher(f"Task payload {i}") for i in range(10)]
    results = [h.result() for h in handles]
    assert len(results) == 10
    assert results[0] == "Gnfx cnlybnq 0"
    assert results[9] == "Gnfx cnlybnq 9"


def test_wasm_cancellation():
    from pyroxide import register_wasm_wat
    WAT_LOOP = """
(module
  (memory (export "memory") 1)
  (func (export "run") (param i32 i32) (result i64)
    (loop br 0)
    i64.const 0
  )
  (func (export "alloc") (param i32) (result i32) i32.const 0)
  (func (export "dealloc") (param i32) (param i32))
)
"""
    register_wasm_wat("loop_cancel_mod", WAT_LOOP)

    @wasm_task("loop_cancel_mod", "run")
    def loop_task(payload: str) -> str:
        pass

    handle = loop_task("start")
    cancelled = handle.cancel()
    assert cancelled is True
    assert handle.status == "Cancelled"

    with pytest.raises(RuntimeError) as exc_info:
        handle.result()
    assert "Task cancelled" in str(exc_info.value)


def test_wasm_oop_proxy():
    """Verifies that load_wasm loads an OOP proxy resolving WASM exports dynamically."""
    from pyroxide import load_wasm

    register_wasm("rot13_oop", WASM_BYTES)
    proxy = load_wasm("rot13_oop")

    handle = proxy.run("Hello OOP WASM!")
    assert handle.result() == "Uryyb BBC JNFZ!"


def test_compile_wat_wasm():
    """Verifies that compile_wasm and compile_wat_wasm register WAT strings and execute correctly."""
    from pyroxide import compile_wasm, load_wasm

    WAT_CODE = """
    (module
      (memory (export "memory") 1)
      (func (export "run") (param i32 i32) (result i64)
        i64.const 0
      )
      (func (export "alloc") (param i32) (result i32)
        i32.const 0
      )
      (func (export "dealloc") (param i32) (param i32)
      )
    )
    """
    mod_name = compile_wasm("test_wat_comp", WAT_CODE, lang="wat")
    proxy = load_wasm(mod_name)
    handle = proxy.run("hello")
    assert handle.result() == ""

