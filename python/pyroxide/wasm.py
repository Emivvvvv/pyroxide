import functools
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager

from ._pyroxide import register_wasm_module, submit_wasm_batch, submit_wasm_task
from .plugins import (
    CrossProcessLock,
    _acquire_compilation_locks,
    _cache_dir,
    _check_compilation_enabled,
    _compile_lock,
    _compiler_timeout_seconds,
    _verify_compiler,
)
from .types import TaskHandle

__all__ = [
    "compile_c_wasm",
    "compile_rust_wasm",
    "compile_wasm",
    "compile_wat_wasm",
    "compile_zig_wasm",
    "load_wasm",
    "register_wasm",
    "register_wasm_wat",
    "wasm_task",
]


@contextmanager
def _wasm_compilation_guard():
    lock = CrossProcessLock(os.path.join(_cache_dir(), "wasm-compile.lock"))
    _acquire_compilation_locks(lock)
    try:
        yield
    finally:
        lock.release()
        _compile_lock.release()


def register_wasm(module_name: str, wasm_bytes: bytes):
    """
    Registers a pre-compiled WebAssembly module in the global registry.
    """
    register_wasm_module(module_name, wasm_bytes)


def register_wasm_wat(module_name: str, wat_str: str):
    """
    Registers a WebAssembly module from WAT text format.
    """
    from ._pyroxide import register_wasm_wat as reg_wat

    reg_wat(module_name, wat_str)


def wasm_task(module_name: str, func_name: str = "run", *, isolated: bool = False):
    """
    Decorator to submit string or byte payloads to be processed by a registered WASM module.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(payload) -> TaskHandle:
            from .config import _local

            wasm_mem = getattr(_local, "wasm_memory_limit_bytes", None)
            wasm_time = getattr(_local, "wasm_timeout_ms", None)
            queue_time = getattr(_local, "queue_timeout_ms", None)
            task_id = submit_wasm_task(
                module_name,
                func_name,
                payload,
                isolated=isolated,
                wasm_memory_limit_bytes=wasm_mem,
                wasm_timeout_ms=wasm_time,
                queue_timeout_ms=queue_time,
            )
            return TaskHandle(task_id)

        return wrapper

    return decorator


class WasmProxy:
    """A proxy representing a registered WebAssembly module."""

    def __init__(self, module_name: str, isolated: bool = False):
        self._module_name = module_name
        self._isolated = isolated

    def __getattr__(self, func_name: str):
        def wasm_method(payload) -> TaskHandle:
            from .config import _local

            wasm_mem = getattr(_local, "wasm_memory_limit_bytes", None)
            wasm_time = getattr(_local, "wasm_timeout_ms", None)
            queue_time = getattr(_local, "queue_timeout_ms", None)
            task_id = submit_wasm_task(
                self._module_name,
                func_name,
                payload,
                isolated=self._isolated,
                wasm_memory_limit_bytes=wasm_mem,
                wasm_timeout_ms=wasm_time,
                queue_timeout_ms=queue_time,
            )
            return TaskHandle(task_id)

        def wasm_batch(payloads: list) -> list[TaskHandle]:
            from .config import _local

            wasm_mem = getattr(_local, "wasm_memory_limit_bytes", None)
            wasm_time = getattr(_local, "wasm_timeout_ms", None)
            queue_time = getattr(_local, "queue_timeout_ms", None)
            task_ids = submit_wasm_batch(
                self._module_name,
                func_name,
                payloads,
                isolated=self._isolated,
                wasm_memory_limit_bytes=wasm_mem,
                wasm_timeout_ms=wasm_time,
                queue_timeout_ms=queue_time,
            )
            return [TaskHandle(task_id) for task_id in task_ids]

        setattr(wasm_method, "batch", wasm_batch)
        return wasm_method


def load_wasm(
    module_name: str,
    *,
    generate_stubs: bool = False,
    isolated: bool = False,
) -> WasmProxy:
    """
    Loads a registered WebAssembly (WASM) module and returns an object-oriented proxy
    allowing direct invocation of any exported WASM function on the background worker pool.
    """
    proxy_class_name = f"{module_name.capitalize()}WasmProxy"
    ProxyClass = type(proxy_class_name, (WasmProxy,), {})
    proxy = ProxyClass(module_name, isolated=isolated)
    if generate_stubs:
        from pyroxide.stubs import generate_stubs as run_gen

        run_gen(module_name, library_type="wasm")
    return proxy


def compile_wat_wasm(module_name: str, wat_code: str) -> str:
    """
    Registers a WebAssembly module from WAT text format string on-the-fly.
    """
    register_wasm_wat(module_name, wat_code)
    return module_name


def compile_c_wasm(module_name: str, source_code: str) -> str:
    """
    Compiles C source code on-the-fly into WebAssembly bytecode (WASM/WASI) and registers it for sandboxed execution.
    """
    stripped = source_code.strip()
    if stripped.startswith("(module"):
        register_wasm_wat(module_name, source_code)
        return module_name

    _check_compilation_enabled()
    cc = os.environ.get("CC", "clang" if sys.platform == "darwin" else "gcc")
    _verify_compiler(cc)

    with _wasm_compilation_guard():
        temp_dir = tempfile.mkdtemp(prefix=f"pyroxide_c_wasm_{module_name}_")
        try:
            src_path = os.path.join(temp_dir, f"{module_name}.c")
            out_path = os.path.join(temp_dir, f"{module_name}.wasm")
            with open(src_path, "w") as f:
                f.write(source_code)

            cmd = [
                cc,
                "--target=wasm32-wasi",
                "-O3",
                "-nostdlib",
                "-Wl,--no-entry",
                "-Wl,--export-all",
                "-o",
                out_path,
                src_path,
            ]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_compiler_timeout_seconds(),
            )
            if res.returncode != 0 or not os.path.exists(out_path):
                cmd_fb = [
                    cc,
                    "--target=wasm32",
                    "-O3",
                    "-nostdlib",
                    "-Wl,--no-entry",
                    "-Wl,--export-all",
                    "-o",
                    out_path,
                    src_path,
                ]
                res_fb = subprocess.run(
                    cmd_fb,
                    capture_output=True,
                    text=True,
                    timeout=_compiler_timeout_seconds(),
                )
                if res_fb.returncode != 0 or not os.path.exists(out_path):
                    raise RuntimeError(
                        f"C to WASM compilation failed:\n{res.stderr}\n{res_fb.stderr}"
                    )

            with open(out_path, "rb") as f:
                wasm_bytes = f.read()

            register_wasm(module_name, wasm_bytes)
            return module_name
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def compile_rust_wasm(module_name: str, source_code: str) -> str:
    """
    Compiles Rust source code on-the-fly into WebAssembly bytecode (WASM/WASI) and registers it for sandboxed execution.
    """
    stripped = source_code.strip()
    if stripped.startswith("(module"):
        register_wasm_wat(module_name, source_code)
        return module_name

    _check_compilation_enabled()
    _verify_compiler("cargo")

    with _wasm_compilation_guard():
        temp_dir = tempfile.mkdtemp(prefix=f"pyroxide_rust_wasm_{module_name}_")
        try:
            subprocess.run(
                ["cargo", "init", "--lib", "--name", module_name],
                cwd=temp_dir,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_compiler_timeout_seconds(),
            )

            cargo_toml_path = os.path.join(temp_dir, "Cargo.toml")
            with open(cargo_toml_path, "r") as f:
                cargo_content = f.read()

            cargo_content = cargo_content.replace(
                'edition = "2024"', 'edition = "2021"'
            )
            cargo_content += '\n[lib]\ncrate-type = ["cdylib"]\n'
            with open(cargo_toml_path, "w") as f:
                f.write(cargo_content)

            lib_rs_path = os.path.join(temp_dir, "src", "lib.rs")
            with open(lib_rs_path, "w") as f:
                f.write(source_code)

            target = "wasm32-wasip1"
            res = subprocess.run(
                ["cargo", "build", "--target", target, "--release"],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=_compiler_timeout_seconds(),
            )
            out_wasm = os.path.join(
                temp_dir, "target", target, "release", f"{module_name}.wasm"
            )

            if res.returncode != 0 or not os.path.exists(out_wasm):
                fallback_target = "wasm32-unknown-unknown"
                res_fb = subprocess.run(
                    ["cargo", "build", "--target", fallback_target, "--release"],
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=_compiler_timeout_seconds(),
                )
                out_wasm = os.path.join(
                    temp_dir,
                    "target",
                    fallback_target,
                    "release",
                    f"{module_name}.wasm",
                )
                if res_fb.returncode != 0 or not os.path.exists(out_wasm):
                    raise RuntimeError(
                        f"Rust to WASM compilation failed:\n{res.stderr}\n{res_fb.stderr}"
                    )

            with open(out_wasm, "rb") as f:
                wasm_bytes = f.read()

            register_wasm(module_name, wasm_bytes)
            return module_name
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def compile_zig_wasm(module_name: str, source_code: str) -> str:
    """
    Compiles Zig source code on-the-fly into WebAssembly bytecode (WASM/WASI) and registers it for sandboxed execution.
    """
    stripped = source_code.strip()
    if stripped.startswith("(module"):
        register_wasm_wat(module_name, source_code)
        return module_name

    _check_compilation_enabled()
    _verify_compiler("zig")

    with _wasm_compilation_guard():
        temp_dir = tempfile.mkdtemp(prefix=f"pyroxide_zig_wasm_{module_name}_")
        try:
            src_path = os.path.join(temp_dir, f"{module_name}.zig")
            out_path = os.path.join(temp_dir, f"{module_name}.wasm")
            with open(src_path, "w") as f:
                f.write(source_code)

            cmd = [
                "zig",
                "build-exe",
                "-target",
                "wasm32-wasi",
                "-O",
                "ReleaseFast",
                f"-femit-bin={out_path}",
                src_path,
            ]
            res = subprocess.run(
                cmd,
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=_compiler_timeout_seconds(),
            )
            if res.returncode != 0 or not os.path.exists(out_path):
                raise RuntimeError(
                    f"Zig to WASM compilation failed:\n{res.stderr}\n{res.stdout}"
                )

            with open(out_path, "rb") as f:
                wasm_bytes = f.read()

            register_wasm(module_name, wasm_bytes)
            return module_name
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def compile_wasm(module_name: str, source_code: str, lang: str = "wat") -> str:
    """
    Compiles and registers source code (WAT, C, Rust, or Zig) to sandboxed WebAssembly (WASM) on-the-fly.
    """
    lang_lower = lang.lower()
    if lang_lower in ("wat", "wasm"):
        return compile_wat_wasm(module_name, source_code)
    elif lang_lower == "c":
        return compile_c_wasm(module_name, source_code)
    elif lang_lower == "rust":
        return compile_rust_wasm(module_name, source_code)
    elif lang_lower == "zig":
        return compile_zig_wasm(module_name, source_code)
    else:
        raise ValueError(
            f"Unsupported WASM compilation language '{lang}'. Supported: 'wat', 'c', 'rust', 'zig'"
        )
