import os
from pyroxide import register_wasm, generate_stubs

WASM_PATH = os.path.join(os.path.dirname(__file__), "resources", "rot13.wasm")

with open(WASM_PATH, "rb") as f:
    WASM_BYTES = f.read()


def test_stub_generation_wasm():
    register_wasm("rot13_stub", WASM_BYTES)

    stub_file = "rot13_stub_proxy.pyi"
    if os.path.exists(stub_file):
        os.remove(stub_file)

    try:
        path = generate_stubs("rot13_stub", "wasm", stub_file)
        assert os.path.exists(path)

        with open(path, "r") as f:
            content = f.read()

        assert "class Rot13_stubWasmProxy:" in content
        assert "def run(self, payload: Any) -> TaskHandle: ..." in content
        assert (
            "def load_wasm_rot13_stub(isolated: bool = ...) -> Rot13_stubWasmProxy: ..."
            in content
        )
    finally:
        if os.path.exists(stub_file):
            os.remove(stub_file)


def test_load_wasm_generate_stubs():
    from pyroxide import load_wasm

    register_wasm("rot13_auto_stub", WASM_BYTES)

    stub_file = "rot13_auto_stub_proxy.pyi"
    if os.path.exists(stub_file):
        os.remove(stub_file)

    try:
        # Pass generate_stubs=True
        _proxy = load_wasm("rot13_auto_stub", generate_stubs=True)
        assert os.path.exists(stub_file)
    finally:
        if os.path.exists(stub_file):
            os.remove(stub_file)


def test_load_dylib_metadata_and_stubs():
    from pyroxide import compile_c, load_dylib

    # C plugin exposing pyroxide_metadata
    C_SRC = """
    #include <stdint.h>
    #include <stddef.h>
    
    int32_t my_sub(int32_t a, int32_t b) {
        return a - b;
    }
    
    const char* pyroxide_metadata() {
        return "my_sub:i32,i32|i32";
    }
    
    void pyroxide_plugin_free(void* ptr, size_t len) {
        // Dummy
    }
    """

    compile_c("dyn_meta", C_SRC)

    stub_file = "dyn_meta_proxy.pyi"
    if os.path.exists(stub_file):
        os.remove(stub_file)

    try:
        # Load without signatures (should auto-discover signatures and generate stubs)
        proxy = load_dylib("dyn_meta", generate_stubs=True)

        # Test signature discovery
        assert "my_sub" in proxy._signatures
        assert proxy._signatures["my_sub"] == {"args": ["i32", "i32"], "ret": "i32"}

        # Test calling the discovered FFI function
        handle = proxy.my_sub(50, 8)
        assert handle.result() == 42

        # Test stub generation
        assert os.path.exists(stub_file)
        with open(stub_file, "r") as f:
            content = f.read()

        assert "class Dyn_metaDylibProxy:" in content
        assert "def my_sub(self, arg0: Any, arg1: Any) -> TaskHandle: ..." in content
    finally:
        if os.path.exists(stub_file):
            os.remove(stub_file)
