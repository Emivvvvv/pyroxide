# -*- coding: utf-8 -*-
from pyroxide import register_wasm, wasm_task

if __name__ == "__main__":
    print("--- 9. WebAssembly Sandboxing Example ---")
    
    # Read the compiled .wasm file bytes (mock bytes here)
    mock_wasm_bytes = b"\x00asm\x01\x00\x00\x00" # Minimal WASM header (will trigger parse error if not valid)
    
    print("Registering WebAssembly module bytes...")
    try:
        register_wasm("math_engine", mock_wasm_bytes)
        
        @wasm_task("math_engine", "add_two")
        def run_wasm_calc(a: int, b: int) -> int:
            pass
        print("WASM Task registered successfully.")
    except Exception as e:
        # Will fail on parse validation due to invalid mock bytes, verifying the check works
        print(f"WebAssembly registration API verified. Error (expected): {e}")
