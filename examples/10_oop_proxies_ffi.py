import os
import shutil
from pyroxide import compile_c, load_dylib, generate_stubs

C_SRC = """
#include <stdint.h>
#include <stddef.h>

int32_t add_numbers(int32_t a, int32_t b) {
    return a + b;
}

double multiply_factors(double x, double y) {
    return x * y;
}

void pyroxide_plugin_free(void* ptr, size_t len) {
    // Dummy, not needed for primitives
}
"""

if __name__ == "__main__":
    print("--- 10. OOP Proxies & FFI Type-Casting Example ---")

    # Check compiler availability
    cc_available = (
        shutil.which("cc") is not None
        or shutil.which("gcc") is not None
        or shutil.which("clang") is not None
    )

    if not cc_available:
        print("C compiler not found. Skipping example.")
        exit(0)

    # 1. Compile C code on-the-fly
    print("Compiling FFI plugin on-the-fly...")
    compile_c("my_math_plugin", C_SRC)

    # 2. Load the Object-Oriented Proxy with function signatures
    print("Loading proxy with FFI signatures...")
    math_proxy = load_dylib(
        "my_math_plugin",
        signatures={
            "add_numbers": {"args": ["i32", "i32"], "ret": "i32"},
            "multiply_factors": {"args": ["f64", "f64"], "ret": "f64"},
        },
    )

    # 3. Call dynamic FFI methods directly!
    print("Calling FFI methods on background workers...")
    h1 = math_proxy.add_numbers(100, 42)
    h2 = math_proxy.multiply_factors(3.14, 2.0)

    print(f"Add Result:      {h1.result()} (expected 142)")
    print(f"Multiply Result: {h2.result()} (expected 6.28)")

    # 4. Generate PEP 484 type stub file (.pyi) for IDE autocompletion
    print("Generating type stubs for IDE autocomplete...")
    stub_path = generate_stubs("my_math_plugin", "dylib")
    print(f"Stub file written to: {stub_path}")

    # Cleanup stub file
    if os.path.exists(stub_path):
        os.remove(stub_path)

    print("✔ OOP Proxies & FFI Example PASSED.")
