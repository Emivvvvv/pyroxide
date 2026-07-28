from pyroxide import register_wasm_wat, wasm_task

IDENTITY_WAT = """
(module
  (memory (export "memory") 1)
  (func (export "alloc") (param i32) (result i32)
    i32.const 0)
  (func (export "dealloc") (param i32 i32))
  (func (export "run") (param i32 i32) (result i64)
    local.get 0
    i64.extend_i32_u
    i64.const 32
    i64.shl
    local.get 1
    i64.extend_i32_u
    i64.or))
"""


if __name__ == "__main__":
    print("--- 9. WebAssembly Bounds and Timeout Example ---")
    register_wasm_wat("identity", IDENTITY_WAT)

    @wasm_task("identity", "run")
    def identity(payload: bytes) -> bytes:
        pass

    assert identity(b"sandboxed data").result() == b"sandboxed data"
    print("WASM identity module completed within configured memory and epoch limits.")
