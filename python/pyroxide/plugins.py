import os
import subprocess
import tempfile
import sys
import shutil
from typing import Dict, Optional, Callable, Any
from ._pyroxide import register_dylib, submit_dylib_task
from .types import TaskHandle


def _verify_compiler(binary: str) -> None:
    """Checks if the required compiler binary is available on the system PATH."""
    if not shutil.which(binary):
        raise RuntimeError(
            f"Required compiler system binary '{binary}' is not found on your PATH. "
            "Please install the compiler or use a pre-compiled binary."
        )


def compile_dylib(
    name: str, source_code: str, dependencies: Optional[Dict[str, str]] = None
) -> str:
    """
    Compiles Rust source code on-the-fly into a dynamic shared library (.so / .dylib / .dll),
    and registers it with the Pyroxide background broker for GIL-free execution.

    The compilation is handled automatically by invoking ``cargo build --release`` inside
    a temporary directory. The user does not need to install or configure anything beyond
    having a working Rust toolchain (``rustc`` + ``cargo``).

    Args:
        name: Unique name for the dylib. Used to reference it in ``@dylib_task``.
        source_code: Raw Rust source code string. Must export two C-compatible symbols:

            - ``pyroxide_plugin_run(ptr, len, out_len) -> *mut u8``
            - ``pyroxide_plugin_free(ptr, len)``
        dependencies: Optional dict of Cargo dependencies, e.g. ``{"serde": "1.0"}``.

    Returns:
        Absolute path to the compiled shared library file.

    Raises:
        RuntimeError: If the Cargo compilation fails.
        FileNotFoundError: If the compiled library binary is not found after build.

    Example:
        >>> compile_dylib("my_lib", RUST_SOURCE_CODE)
        >>> @dylib_task("my_lib")
        ... def process(payload): pass
        >>> handle = process("hello")
        >>> print(handle.result())
    """
    _verify_compiler("cargo")

    temp_dir = tempfile.mkdtemp(prefix=f"pyroxide_dylib_{name}_")
    try:
        # Run cargo init
        subprocess.run(
            ["cargo", "init", "--lib", "--name", name],
            cwd=temp_dir,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        cargo_toml_path = os.path.join(temp_dir, "Cargo.toml")
        with open(cargo_toml_path, "r") as f:
            cargo_content = f.read()

        # Force Edition 2021 to prevent newer Rust 2024 edition strict compiler errors
        cargo_content = cargo_content.replace('edition = "2024"', 'edition = "2021"')

        # Add cdylib configuration
        cargo_content += '\n[lib]\ncrate-type = ["cdylib"]\n'

        # Add dependencies
        if dependencies:
            cargo_content += "\n[dependencies]\n"
            for dep, ver in dependencies.items():
                cargo_content += f'{dep} = "{ver}"\n'

        with open(cargo_toml_path, "w") as f:
            f.write(cargo_content)

        # Write Rust source code to src/lib.rs
        lib_rs_path = os.path.join(temp_dir, "src", "lib.rs")
        with open(lib_rs_path, "w") as f:
            f.write(source_code)

        # Run cargo build in release mode
        res = subprocess.run(
            ["cargo", "build", "--release"],
            cwd=temp_dir,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise RuntimeError(f"Cargo build failed:\n{res.stderr}\n{res.stdout}")

        # Find compiled library
        lib_ext = "dylib" if sys.platform == "darwin" else "so"
        if sys.platform == "win32":
            lib_ext = "dll"

        lib_name = f"lib{name}.{lib_ext}"
        if sys.platform == "win32":
            lib_name = f"{name}.{lib_ext}"

        compiled_path = os.path.join(temp_dir, "target", "release", lib_name)
        if not os.path.exists(compiled_path):
            raise FileNotFoundError(f"Compiled library not found at: {compiled_path}")

        # Copy to persistent cache directory
        cache_dir = os.path.expanduser("~/.pyroxide/cache")
        os.makedirs(cache_dir, exist_ok=True)
        dest_path = os.path.join(cache_dir, lib_name)
        shutil.copy2(compiled_path, dest_path)

        # Register dylib with the Rust core engine
        register_dylib(name, dest_path)
        return dest_path

    except Exception as e:
        raise RuntimeError(f"Failed to compile dylib '{name}' via Cargo: {e}") from e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def compile_c(name: str, source_code: str) -> str:
    """
    Compiles C source code on-the-fly into a dynamic shared library (.so / .dylib / .dll),
    and registers it with the Pyroxide background broker for GIL-free execution.

    Args:
        name: Unique name for the library. Used to reference it in @dylib_task.
        source_code: Raw C source code string. Must export two functions:
            - ``pyroxide_plugin_run(ptr, len, out_len) -> uint8_t*``
            - ``pyroxide_plugin_free(ptr, len)``
    """
    cc = os.environ.get("CC", "clang" if sys.platform == "darwin" else "gcc")
    _verify_compiler(cc)

    temp_dir = tempfile.mkdtemp(prefix=f"pyroxide_c_{name}_")
    try:
        src_path = os.path.join(temp_dir, f"{name}.c")
        with open(src_path, "w") as f:
            f.write(source_code)
        lib_ext = "dylib" if sys.platform == "darwin" else "so"
        if sys.platform == "win32":
            lib_ext = "dll"

        lib_name = f"lib{name}.{lib_ext}"
        if sys.platform == "win32":
            lib_name = f"{name}.{lib_ext}"
        compiled_path = os.path.join(temp_dir, lib_name)

        cmd = [cc, "-shared", "-o", compiled_path, "-fPIC", src_path]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"C compilation failed:\n{res.stderr}\n{res.stdout}")

        if not os.path.exists(compiled_path):
            raise FileNotFoundError(f"Compiled C library not found at: {compiled_path}")

        # Copy to persistent cache directory
        cache_dir = os.path.expanduser("~/.pyroxide/cache")
        os.makedirs(cache_dir, exist_ok=True)
        dest_path = os.path.join(cache_dir, lib_name)
        shutil.copy2(compiled_path, dest_path)

        register_dylib(name, dest_path)
        return dest_path

    except Exception as e:
        raise RuntimeError(f"Failed to compile C library '{name}': {e}") from e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def compile_zig(name: str, source_code: str) -> str:
    """
    Compiles Zig source code on-the-fly into a dynamic shared library (.so / .dylib / .dll),
    and registers it with the Pyroxide background broker for GIL-free execution.

    Args:
        name: Unique name for the library. Used to reference it in @dylib_task.
        source_code: Raw Zig source code string. Must export two functions:
            - ``pyroxide_plugin_run(ptr, len, out_len) -> [*]u8``
            - ``pyroxide_plugin_free(ptr, len)``
    """
    _verify_compiler("zig")

    temp_dir = tempfile.mkdtemp(prefix=f"pyroxide_zig_{name}_")
    try:
        src_path = os.path.join(temp_dir, f"{name}.zig")
        with open(src_path, "w") as f:
            f.write(source_code)

        # Compiles dynamic library. Zig build-lib generates output in cwd
        cmd = ["zig", "build-lib", "-dynamic", "-O", "ReleaseFast", src_path]
        res = subprocess.run(cmd, cwd=temp_dir, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Zig compilation failed:\n{res.stderr}\n{res.stdout}")

        lib_ext = "dylib" if sys.platform == "darwin" else "so"
        if sys.platform == "win32":
            lib_ext = "dll"

        lib_name = f"lib{name}.{lib_ext}"
        if sys.platform == "win32":
            lib_name = f"{name}.{lib_ext}"

        compiled_path = os.path.join(temp_dir, lib_name)
        if not os.path.exists(compiled_path):
            raise FileNotFoundError(
                f"Compiled Zig library not found at: {compiled_path}"
            )

        # Copy to persistent cache directory
        cache_dir = os.path.expanduser("~/.pyroxide/cache")
        os.makedirs(cache_dir, exist_ok=True)
        dest_path = os.path.join(cache_dir, lib_name)
        shutil.copy2(compiled_path, dest_path)

        register_dylib(name, dest_path)
        return dest_path

    except Exception as e:
        raise RuntimeError(f"Failed to compile Zig library '{name}': {e}") from e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def dylib_task(
    dylib_name: str,
    symbol_name: str = "pyroxide_plugin_run",
    ffi_sig: Optional[tuple] = None,
    *,
    isolated: bool = False,
):
    """
    Decorator that routes task payloads to a registered dynamic shared library (dylib)
    for GIL-free execution on the background Rust worker pool.

    The dylib must have been previously compiled and registered via ``compile_dylib()``.

    Args:
        dylib_name: The name of the dylib as registered with ``compile_dylib()``.
        symbol_name: The function symbol to load from the dylib. Defaults to "pyroxide_plugin_run".
        ffi_sig: Optional FFI signature tuple, e.g. (['i32', 'i32'], 'i32')
        isolated: Set to True to run in an isolated worker process for crash isolation.
    """

    def decorator(func: Callable[[Any], Any]) -> Callable[[Any], TaskHandle]:
        def wrapper(payload: Any) -> TaskHandle:
            task_id = submit_dylib_task(
                dylib_name,
                symbol_name,
                payload,
                ffi_sig=ffi_sig,
                isolated=isolated,
            )
            return TaskHandle(task_id)

        def batch(payloads: list) -> list[TaskHandle]:
            return [wrapper(p) for p in payloads]

        wrapper.batch = batch
        return wrapper

    return decorator


class DylibProxy:
    """A proxy representing a dynamically loaded shared library."""

    def __init__(
        self, lib_name: str, signatures: Optional[dict] = None, isolated: bool = False
    ):
        self._lib_name = lib_name
        self._signatures = signatures or {}
        self._isolated = isolated

    def __getattr__(self, symbol_name: str):
        sig = self._signatures.get(symbol_name)
        if sig:
            # FFI custom signature call
            args_types = sig.get("args", [])
            ret_type = sig.get("ret", "void")

            type_mapping = {
                "i32": "i",
                "i64": "q",
                "f32": "f",
                "f64": "d",
            }

            pack_format = "=" + "".join(type_mapping[t] for t in args_types)
            unpack_format = "=" + type_mapping.get(ret_type, "")

            def ffi_method(*args) -> TaskHandle:
                import struct

                packed_payload = struct.pack(pack_format, *args)
                ffi_sig_arg = (args_types, ret_type)

                task_id = submit_dylib_task(
                    self._lib_name,
                    symbol_name,
                    packed_payload,
                    ffi_sig=ffi_sig_arg,
                    isolated=self._isolated,
                )

                handle = TaskHandle(task_id)
                original_result = handle.result
                original_result_async = handle.result_async

                def ffi_result(
                    timeout_sec: Optional[float] = None, consume: bool = True
                ) -> Any:
                    res_bytes = original_result(
                        timeout_sec=timeout_sec, consume=consume
                    )
                    if not unpack_format or unpack_format == "=":
                        return None
                    return struct.unpack(unpack_format, res_bytes)[0]

                async def ffi_result_async(
                    timeout_sec: Optional[float] = None, consume: bool = True
                ) -> Any:
                    res_bytes = await original_result_async(
                        timeout_sec=timeout_sec, consume=consume
                    )
                    if not unpack_format or unpack_format == "=":
                        return None
                    return struct.unpack(unpack_format, res_bytes)[0]

                handle.result = ffi_result
                handle.result_async = ffi_result_async
                return handle

            def ffi_batch(payloads: list) -> list[TaskHandle]:
                res = []
                for p in payloads:
                    if isinstance(p, tuple):
                        res.append(ffi_method(*p))
                    else:
                        res.append(ffi_method(p))
                return res

            ffi_method.batch = ffi_batch
            return ffi_method
        else:
            # Regular bytes/string call
            def dylib_method(payload) -> TaskHandle:
                task_id = submit_dylib_task(
                    self._lib_name,
                    symbol_name,
                    payload,
                    ffi_sig=None,
                    isolated=self._isolated,
                )
                return TaskHandle(task_id)

            def dylib_batch(payloads: list) -> list[TaskHandle]:
                return [dylib_method(p) for p in payloads]

            dylib_method.batch = dylib_batch
            return dylib_method


def load_dylib(
    lib_name: str,
    *,
    signatures: Optional[dict] = None,
    generate_stubs: bool = False,
    isolated: bool = False,
) -> DylibProxy:
    """
    Loads a registered dynamic shared library (dylib) and returns an object-oriented proxy
    allowing direct invocation of any C-ABI exported symbol on the background worker pool.
    """
    # 1. Auto-discover signatures if none are provided
    if signatures is None:
        from pyroxide._pyroxide import get_dylib_metadata

        metadata_str = get_dylib_metadata(lib_name)
        if metadata_str:
            signatures = {}
            for entry in metadata_str.split(";"):
                if not entry:
                    continue
                func_parts = entry.split(":")
                if len(func_parts) == 2:
                    func_name, sig_part = func_parts
                    sig_parts = sig_part.split("|")
                    if len(sig_parts) == 2:
                        args_part, ret_type = sig_parts
                        args = [a for a in args_part.split(",") if a]
                        signatures[func_name] = {"args": args, "ret": ret_type}

    # 2. Create the proxy
    proxy = DylibProxy(lib_name, signatures=signatures, isolated=isolated)

    # 3. Generate stubs if requested
    if generate_stubs:
        from pyroxide.stubs import generate_stubs as run_gen

        run_gen(lib_name, library_type="dylib")

    return proxy
