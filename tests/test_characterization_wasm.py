import os

import pytest
from pyroxide import (
    load_wasm,
    register_wasm,
    register_wasm_wat,
    wasm_task,
)

WASM_PATH = os.path.join(os.path.dirname(__file__), "resources", "rot13.wasm")
with open(WASM_PATH, "rb") as f:
    WASM_BYTES = f.read()


def test_wasm_raw_and_wat_registration():
    register_wasm("char_rot13_bytes", WASM_BYTES)
    wat_code = """
(module
  (memory (export "memory") 1)
  (func (export "run") (param i32 i32) (result i64)
    i64.const 0
  )
  (func (export "alloc") (param i32) (result i32) i32.const 0)
  (func (export "dealloc") (param i32) (param i32))
)
"""
    register_wasm_wat("char_wat_mod", wat_code)


@pytest.mark.parametrize("isolated", [False, True])
def test_wasm_single_and_batch_execution(isolated):
    mod_name = f"char_rot13_exec_{isolated}"
    register_wasm(mod_name, WASM_BYTES)

    @wasm_task(mod_name, "run", isolated=isolated)
    def cipher(payload: str) -> str:
        pass

    h = cipher("Pyroxide WASM Characterization")
    assert h.result() == "Clebkvqr JNFZ Punenpgrevmngvba"

    # Batch submission via proxy
    proxy = load_wasm(mod_name, isolated=isolated)
    handles = proxy.run.batch(["Foo", "Bar"])
    results = [h.result() for h in handles]
    assert results == ["Sbb", "One"]


def test_wasm_proxy_loading():
    mod_name = "char_rot13_proxy"
    register_wasm(mod_name, WASM_BYTES)
    proxy = load_wasm(mod_name)
    assert hasattr(proxy, "run")
    handle = proxy.run("Proxy test")
    assert handle.result() == "Cebkl grfg"


def test_wasm_missing_module_or_export():
    with pytest.raises(RuntimeError, match="not registered"):
        @wasm_task("nonexistent_wasm_module_xyz", "run")
        def missing_mod(payload: str) -> str:
            pass

        missing_mod("test").result()

    mod_name = "char_rot13_missing_export"
    register_wasm(mod_name, WASM_BYTES)
    @wasm_task(mod_name, "no_such_export")
    def missing_exp(payload: str) -> str:
        pass

    with pytest.raises(RuntimeError, match="missing export"):
        missing_exp("test").result()


def test_wasm_invalid_bytecode():
    with pytest.raises(ValueError, match="Failed to compile WASM module"):
        register_wasm("invalid_wasm_mod", b"NOT_A_VALID_WASM")
