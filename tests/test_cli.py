import pytest
import os
import tempfile
import subprocess
import shutil
import sys

# We'll use the infinite loop WAT to test WASM stub generation
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

def test_cli_stub_compilation_from_scan():
    temp_dir = tempfile.mkdtemp(prefix="pyroxide_cli_test_")
    try:
        # 1. Write the WASM module file
        wasm_path = os.path.join(temp_dir, "test_module.wasm")
        # Compile WAT to WASM using wat2wasm if possible, or just write mock WAT and use register_wasm_wat.
        # Wait, our CLI tool supports compiling from register_wasm_wat!
        # So we can write a python file that registers it via register_wasm_wat:
        py_content = f"""
import pyroxide
pyroxide.register_wasm_wat("test_cli_wasm", r'''{WAT_CODE}''')
"""
        py_file = os.path.join(temp_dir, "test_app.py")
        with open(py_file, "w") as f:
            f.write(py_content)

        # 2. Run the CLI tool using subprocess
        # python -m pyroxide.cli build-stubs --scan --scan-dir <temp_dir> --out-dir <temp_dir> --no-pyproject
        python_bin = sys.executable
        cmd = [
            python_bin,
            "-m",
            "pyroxide.cli",
            "build-stubs",
            "--scan",
            "--scan-dir",
            temp_dir,
            "--out-dir",
            temp_dir,
            "--no-pyproject"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"CLI failed: {res.stderr}\n{res.stdout}"

        # 3. Verify files were generated
        pyi_path = os.path.join(temp_dir, "test_cli_wasm_proxy.pyi")
        py_path = os.path.join(temp_dir, "test_cli_wasm_proxy.py")

        assert os.path.exists(pyi_path), "Proxy .pyi file was not generated"
        assert os.path.exists(py_path), "Proxy .py file was not generated"

        with open(pyi_path, "r") as f:
            pyi_content = f.read()
        assert "class Test_cli_wasmWasmProxy:" in pyi_content
        assert "def run(self, payload: Any) -> TaskHandle:" in pyi_content

        with open(py_path, "r") as f:
            py_content_read = f.read()
        assert "class Test_cli_wasmWasmProxy(WasmProxy):" in py_content_read

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_cli_stub_compilation_from_pyproject():
    temp_dir = tempfile.mkdtemp(prefix="pyroxide_cli_pyproject_test_")
    try:
        # 1. Write a WAT file
        wat_path = os.path.join(temp_dir, "test_mod.wat")
        with open(wat_path, "w") as f:
            f.write(WAT_CODE)

        # 2. Write a mock pyproject.toml in the temp directory
        pyproject_content = f"""
[tool.pyroxide.stubs]
test_pyproj_wasm = {{ type = "wat", wat = '''{WAT_CODE}''' }}
"""
        pyproject_path = os.path.join(temp_dir, "pyproject.toml")
        with open(pyproject_path, "w") as f:
            f.write(pyproject_content)

        # 3. Run the CLI tool using subprocess, passing pyproject path
        python_bin = sys.executable
        cmd = [
            python_bin,
            "-m",
            "pyroxide.cli",
            "build-stubs",
            "--pyproject",
            pyproject_path,
            "--out-dir",
            temp_dir
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"CLI failed: {res.stderr}\n{res.stdout}"

        # 4. Verify files were generated
        pyi_path = os.path.join(temp_dir, "test_pyproj_wasm_proxy.pyi")
        py_path = os.path.join(temp_dir, "test_pyproj_wasm_proxy.py")

        assert os.path.exists(pyi_path), "Proxy .pyi file from pyproject was not generated"
        assert os.path.exists(py_path), "Proxy .py file from pyproject was not generated"

        with open(pyi_path, "r") as f:
            pyi_content = f.read()
        assert "class Test_pyproj_wasmWasmProxy:" in pyi_content

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_cli_warn_missing_deallocator(capsys):
    """Verifies that generate_stubs prints a warning to stderr when a library lacks a deallocator for raw tasks."""
    from pyroxide import compile_rust, generate_stubs

    RUST_NO_FREE_SRC = """
    #[no_mangle]
    pub extern "C" fn add_ints(a: i32, b: i32) -> i32 {
        a + b
    }
    """

    # 1. Compile the dylib without free_fn
    compile_rust("dyn_warn_lib", RUST_NO_FREE_SRC)

    # 2. Call generate_stubs and capture output
    temp_dir = tempfile.mkdtemp(prefix="pyroxide_cli_warn_")
    try:
        out_path = os.path.join(temp_dir, "dyn_warn_lib_proxy.pyi")
        generate_stubs("dyn_warn_lib", "dylib", out_path=out_path)
        
        # Capture stdout/stderr
        captured = capsys.readouterr()
        
        # Verify warning is printed to stderr
        assert "Warning: Library 'dyn_warn_lib' exposes raw binary tasks" in captured.err
        assert "does not export 'pyroxide_plugin_free'" in captured.err
        
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

