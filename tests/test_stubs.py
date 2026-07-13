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
        assert "def load_wasm_rot13_stub(isolated: bool = ...) -> Rot13_stubWasmProxy: ..." in content
    finally:
        if os.path.exists(stub_file):
            os.remove(stub_file)
