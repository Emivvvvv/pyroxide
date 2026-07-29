"""Native compiler discovery, locking, caching, and invocation."""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Dict, Optional

from ._pyroxide import register_dylib

__all__ = [
    "CompilerNotFoundError",
    "CrossProcessLock",
    "compile_c",
    "compile_rust",
    "compile_zig",
]

_compile_lock = threading.Lock()


def _cache_dir() -> str:
    configured = os.environ.get("PYROXIDE_CACHE_DIR")
    return os.path.abspath(os.path.expanduser(configured or "~/.pyroxide/cache"))


def _compiler_timeout_seconds() -> float:
    try:
        timeout = float(os.environ.get("PYROXIDE_COMPILER_TIMEOUT_SEC", "300"))
    except ValueError:
        timeout = 300.0
    return timeout if timeout > 0 else 300.0


def _acquire_compilation_locks(lock: "CrossProcessLock") -> None:
    _compile_lock.acquire()
    try:
        lock.acquire()
    except BaseException:
        _compile_lock.release()
        raise


def _publish_library(compiled_path: str, cache_dir: str, lib_name: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    dest_path = os.path.join(cache_dir, lib_name)
    fd, temp_path = tempfile.mkstemp(prefix=f".{lib_name}.", dir=cache_dir)
    os.close(fd)
    try:
        shutil.copy2(compiled_path, temp_path)
        os.replace(temp_path, dest_path)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
    return dest_path


class CrossProcessLock:
    def __init__(self, lock_path: str, timeout: float = 60.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self.locked = False

    def _is_pid_running(self, pid: int) -> bool:
        if sys.platform == "win32":
            try:
                import ctypes

                handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return True
                return ctypes.windll.kernel32.GetLastError() == 5  # Access Denied
            except Exception:
                return True  # Fallback to safe
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    def acquire(self):
        start = time.time()
        parent_dir = os.path.dirname(self.lock_path)
        os.makedirs(parent_dir, exist_ok=True)
        pid_file = os.path.join(self.lock_path, "owner.pid")
        my_pid = os.getpid()
        while True:
            try:
                os.mkdir(self.lock_path)
                with open(pid_file, "w") as f:
                    f.write(str(my_pid))
                self.locked = True
                return True
            except FileExistsError:
                try:
                    with open(pid_file, "r") as f:
                        owner_pid = int(f.read().strip())
                except (OSError, ValueError):
                    owner_pid = None

                if owner_pid is not None and not self._is_pid_running(owner_pid):
                    try:
                        os.remove(pid_file)
                    except OSError:
                        pass
                    try:
                        os.rmdir(self.lock_path)
                    except OSError:
                        pass
                    continue

                if time.time() - start > self.timeout:
                    raise TimeoutError(
                        f"Timeout waiting for compilation lock at {self.lock_path}"
                    )
                time.sleep(0.05)

    def release(self):
        if self.locked:
            pid_file = os.path.join(self.lock_path, "owner.pid")
            try:
                os.remove(pid_file)
            except OSError:
                pass
            try:
                os.rmdir(self.lock_path)
            except OSError:
                pass
            self.locked = False


class CompilerNotFoundError(RuntimeError):
    """Raised when a required compiler binary (cargo, gcc, clang, zig) is missing from PATH."""

    pass


def _verify_compiler(binary: str) -> None:
    """Checks if the required compiler binary is available on the system PATH."""
    if not shutil.which(binary):
        raise CompilerNotFoundError(
            f"Required compiler system binary '{binary}' is not found on your PATH. "
            "Please install the compiler toolchain or use pre-compiled binaries."
        )


def _check_compilation_enabled() -> None:
    if os.environ.get("PYROXIDE_DISABLE_COMPILATION") in ("1", "true", "TRUE"):
        raise PermissionError("On-the-fly compilation is disabled in this environment.")


def compile_rust(
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
        >>> compile_rust("my_lib", RUST_SOURCE_CODE)
        >>> @dylib_task("my_lib")
        ... def process(payload): pass
        >>> handle = process("hello")
        >>> print(handle.result())
    """
    _check_compilation_enabled()
    _verify_compiler("cargo")

    cache_dir = _cache_dir()
    lock_path = os.path.join(cache_dir, "compile.lock")
    lock = CrossProcessLock(lock_path)
    _acquire_compilation_locks(lock)

    temp_dir = tempfile.mkdtemp(prefix=f"pyroxide_dylib_{name}_")
    try:
        # Run cargo init
        subprocess.run(
            ["cargo", "init", "--lib", "--name", name],
            cwd=temp_dir,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_compiler_timeout_seconds(),
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
            timeout=_compiler_timeout_seconds(),
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
        dest_path = _publish_library(compiled_path, cache_dir, lib_name)

        # Register dylib with the Rust core engine
        register_dylib(name, dest_path)
        return dest_path

    except Exception as e:
        raise RuntimeError(f"Failed to compile dylib '{name}' via Cargo: {e}") from e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        lock.release()
        _compile_lock.release()


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
    _check_compilation_enabled()
    cc = os.environ.get("CC", "clang" if sys.platform == "darwin" else "gcc")
    _verify_compiler(cc)

    cache_dir = _cache_dir()
    lock_path = os.path.join(cache_dir, "compile.lock")
    lock = CrossProcessLock(lock_path)
    _acquire_compilation_locks(lock)

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
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_compiler_timeout_seconds(),
        )
        if res.returncode != 0:
            raise RuntimeError(f"C compilation failed:\n{res.stderr}\n{res.stdout}")

        if not os.path.exists(compiled_path):
            raise FileNotFoundError(f"Compiled C library not found at: {compiled_path}")

        # Copy to persistent cache directory
        dest_path = _publish_library(compiled_path, cache_dir, lib_name)

        register_dylib(name, dest_path)
        return dest_path

    except Exception as e:
        raise RuntimeError(f"Failed to compile C library '{name}': {e}") from e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        lock.release()
        _compile_lock.release()


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
    _check_compilation_enabled()
    _verify_compiler("zig")

    cache_dir = _cache_dir()
    lock_path = os.path.join(cache_dir, "compile.lock")
    lock = CrossProcessLock(lock_path)
    _acquire_compilation_locks(lock)

    temp_dir = tempfile.mkdtemp(prefix=f"pyroxide_zig_{name}_")
    try:
        src_path = os.path.join(temp_dir, f"{name}.zig")
        with open(src_path, "w") as f:
            f.write(source_code)

        # Compiles dynamic library. Zig build-lib generates output in cwd
        cmd = ["zig", "build-lib", "-dynamic", "-O", "ReleaseFast", src_path]
        res = subprocess.run(
            cmd,
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=_compiler_timeout_seconds(),
        )
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
        dest_path = _publish_library(compiled_path, cache_dir, lib_name)

        register_dylib(name, dest_path)
        return dest_path

    except Exception as e:
        raise RuntimeError(f"Failed to compile Zig library '{name}': {e}") from e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        lock.release()
        _compile_lock.release()

