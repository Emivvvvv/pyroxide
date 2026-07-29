import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Optional

from ._ffi_types import build_argument_format, build_return_format, validate_ffi_type
from ._pyroxide import register_dylib, submit_dylib_batch, submit_dylib_task
from .types import TaskHandle

__all__ = [
    "CompilerNotFoundError",
    "compile_c",
    "compile_rust",
    "compile_zig",
    "dylib_task",
    "load_dylib",
    "unregister_dylib",
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

    The dylib must have been previously compiled and registered via ``compile_rust()``.

    Args:
        dylib_name: The name of the dylib as registered with ``compile_rust()``.
        symbol_name: The function symbol to load from the dylib. Defaults to "pyroxide_plugin_run".
        ffi_sig: Optional FFI signature tuple, e.g. (['i32', 'i32'], 'i32')
        isolated: Set to True to run in an isolated worker process for crash isolation.
    """

    def decorator(func: Callable[[Any], Any]) -> Callable[[Any], TaskHandle]:
        def wrapper(payload: Any) -> TaskHandle:
            from .config import get_scoped_queue_timeout_ms

            queue_time = get_scoped_queue_timeout_ms()
            task_id = submit_dylib_task(
                dylib_name,
                symbol_name,
                payload,
                ffi_sig=ffi_sig,
                isolated=isolated,
                queue_timeout_ms=queue_time,
            )
            return TaskHandle(task_id)

        def batch(payloads: list) -> list[TaskHandle]:
            from .config import get_scoped_queue_timeout_ms

            queue_time = get_scoped_queue_timeout_ms()
            task_ids = submit_dylib_batch(
                dylib_name,
                symbol_name,
                payloads,
                ffi_sig=ffi_sig,
                isolated=isolated,
                queue_timeout_ms=queue_time,
            )
            return [TaskHandle(task_id) for task_id in task_ids]

        setattr(wrapper, "batch", batch)
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
            import struct

            # FFI custom signature call
            args_types = sig.get("args", [])
            if "ret" not in sig:
                raise ValueError(f"FFI signature for '{symbol_name}' must declare 'ret'.")
            ret_type = sig["ret"]

            if len(args_types) > 8:
                raise ValueError(
                    f"FFI signatures support at most 8 arguments; received {len(args_types)}."
                )

            try:
                for t in args_types:
                    validate_ffi_type(t)
                validate_ffi_type(ret_type)
            except ValueError as e:
                raise ValueError(
                    f"Unsupported FFI type for symbol '{symbol_name}': {e}"
                ) from e

            pack_format = build_argument_format(args_types)
            unpack_format = build_return_format(ret_type)
            expected_ret_len = struct.calcsize(unpack_format)

            def ffi_handle(task_id: int) -> TaskHandle:
                handle = TaskHandle(task_id)
                original_result = handle.result

                def ffi_result(
                    timeout_sec: Optional[float] = None, consume: bool = True
                ) -> Any:
                    res_bytes = original_result(
                        timeout_sec=timeout_sec, consume=consume
                    )
                    if len(res_bytes) != expected_ret_len:
                        raise RuntimeError(
                            f"FFI symbol '{symbol_name}' returned {len(res_bytes)} bytes, but {ret_type} requires {expected_ret_len} bytes."
                        )
                    return struct.unpack(unpack_format, res_bytes)[0]

                setattr(handle, "result", ffi_result)
                return handle

            def ffi_method(*args) -> TaskHandle:
                from .config import get_scoped_queue_timeout_ms

                if len(args) != len(args_types):
                    raise ValueError(
                        f"FFI symbol '{symbol_name}' expects {len(args_types)} arguments, received {len(args)}."
                    )

                try:
                    packed_payload = struct.pack(pack_format, *args)
                except struct.error as e:
                    raise ValueError(
                        f"Argument range or type error for FFI symbol '{symbol_name}': {e}"
                    ) from e

                ffi_sig_arg = (args_types, ret_type)

                queue_time = get_scoped_queue_timeout_ms()
                task_id = submit_dylib_task(
                    self._lib_name,
                    symbol_name,
                    packed_payload,
                    ffi_sig=ffi_sig_arg,
                    isolated=self._isolated,
                    queue_timeout_ms=queue_time,
                )

                return ffi_handle(task_id)

            def ffi_batch(payloads: list) -> list[TaskHandle]:
                from .config import get_scoped_queue_timeout_ms

                def pack_payload(payload):
                    args: tuple[Any, ...]
                    if not args_types and (payload == () or payload == [] or payload is None):
                        args = ()
                    elif isinstance(payload, tuple):
                        args = payload
                    else:
                        args = (payload,)

                    if len(args) != len(args_types):
                        raise ValueError(
                            f"FFI symbol '{symbol_name}' expects {len(args_types)} arguments, received {len(args)}."
                        )
                    try:
                        return struct.pack(pack_format, *args)
                    except struct.error as e:
                        raise ValueError(
                            f"Argument range or type error for FFI symbol '{symbol_name}': {e}"
                        ) from e

                ffi_sig_arg = (args_types, ret_type)
                queue_time = get_scoped_queue_timeout_ms()
                task_ids = submit_dylib_batch(
                    self._lib_name,
                    symbol_name,
                    payloads,
                    ffi_sig=ffi_sig_arg,
                    isolated=self._isolated,
                    queue_timeout_ms=queue_time,
                    payload_builder=pack_payload,
                )
                return [ffi_handle(task_id) for task_id in task_ids]

            setattr(ffi_method, "batch", ffi_batch)
            return ffi_method
        else:
            # Regular bytes/string call
            def dylib_method(payload) -> TaskHandle:
                from .config import get_scoped_queue_timeout_ms

                queue_time = get_scoped_queue_timeout_ms()
                task_id = submit_dylib_task(
                    self._lib_name,
                    symbol_name,
                    payload,
                    ffi_sig=None,
                    isolated=self._isolated,
                    queue_timeout_ms=queue_time,
                )
                return TaskHandle(task_id)

            def dylib_batch(payloads: list) -> list[TaskHandle]:
                from .config import get_scoped_queue_timeout_ms

                queue_time = get_scoped_queue_timeout_ms()
                task_ids = submit_dylib_batch(
                    self._lib_name,
                    symbol_name,
                    payloads,
                    ffi_sig=None,
                    isolated=self._isolated,
                    queue_timeout_ms=queue_time,
                )
                return [TaskHandle(task_id) for task_id in task_ids]

            setattr(dylib_method, "batch", dylib_batch)
            return dylib_method


def load_dylib(
    lib_name: str,
    *,
    signatures: Optional[dict] = None,
    generate_stubs: bool = False,
    isolated: bool = False,
    free_fn_name: Optional[str] = None,
) -> DylibProxy:
    """
    Loads a registered dynamic shared library (dylib) and returns an object-oriented proxy
    allowing direct invocation of any C-ABI exported symbol on the background worker pool.
    """
    # 0. Auto-register if not already registered
    try:
        from pyroxide._pyroxide import get_dylib_exports, get_dylib_path

        get_dylib_exports(lib_name)
        if free_fn_name is not None:
            try:
                reg_path = get_dylib_path(lib_name)
                if reg_path:
                    clean_path = reg_path.split(";")[0]
                    register_dylib(lib_name, clean_path, free_fn_name=free_fn_name)
            except Exception:
                pass
    except ValueError:
        try:
            register_dylib(lib_name, lib_name, free_fn_name=free_fn_name)
        except Exception:
            pass

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
    proxy_class_name = f"{lib_name.capitalize()}DylibProxy"
    ProxyClass = type(proxy_class_name, (DylibProxy,), {})
    proxy = ProxyClass(lib_name, signatures=signatures, isolated=isolated)

    # 3. Generate stubs if requested
    if generate_stubs:
        from pyroxide.stubs import generate_stubs as run_gen

        run_gen(lib_name, library_type="dylib")

    return proxy


def unregister_dylib(name: str) -> None:
    """
    Unregisters a dynamic shared library from the Pyroxide registries.
    """
    from pyroxide._pyroxide import unregister_dylib as _raw_unregister

    _raw_unregister(name)
