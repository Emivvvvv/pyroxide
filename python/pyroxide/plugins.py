import os
import subprocess
import tempfile
import sys
from typing import Dict, Optional, Callable, Any
from ._pyroxide import register_dylib, submit_dylib_task
from .types import TaskHandle


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

        # Register dylib with the Rust core engine
        register_dylib(name, compiled_path)
        return compiled_path

    except Exception as e:
        raise RuntimeError(f"Failed to compile dylib '{name}' via Cargo: {e}")


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
    temp_dir = tempfile.mkdtemp(prefix=f"pyroxide_c_{name}_")
    try:
        src_path = os.path.join(temp_dir, f"{name}.c")
        with open(src_path, "w") as f:
            f.write(source_code)

        cc = os.environ.get("CC", "clang" if sys.platform == "darwin" else "gcc")
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

        register_dylib(name, compiled_path)
        return compiled_path

    except Exception as e:
        raise RuntimeError(f"Failed to compile C library '{name}': {e}")


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

        register_dylib(name, compiled_path)
        return compiled_path

    except Exception as e:
        raise RuntimeError(f"Failed to compile Zig library '{name}': {e}")


def dylib_task(dylib_name: str):
    """
    Decorator that routes task payloads to a registered dynamic shared library (dylib)
    for GIL-free execution on the background Rust worker pool.

    The dylib must have been previously compiled and registered via ``compile_dylib()``.

    Args:
        dylib_name: The name of the dylib as registered with ``compile_dylib()``.

    Example:
        >>> @dylib_task("my_lib")
        ... def process(payload: str) -> str:
        ...     pass  # Execution is handled by the compiled dylib
        >>> handle = process("hello")
        >>> print(handle.result())
    """

    def decorator(func: Callable[[Any], Any]) -> Callable[[Any], TaskHandle]:
        def wrapper(payload: Any) -> TaskHandle:
            task_id = submit_dylib_task(dylib_name, payload)
            return TaskHandle(task_id)

        def batch(payloads: list) -> list[TaskHandle]:
            return [wrapper(p) for p in payloads]

        wrapper.batch = batch
        return wrapper

    return decorator
