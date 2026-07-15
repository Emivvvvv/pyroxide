import argparse
import os
import sys
import ast
from typing import Dict, Any
import pyroxide
from pyroxide.stubs import generate_stubs

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


def parse_pyproject(path: str = "pyproject.toml") -> Dict[str, Any]:
    """Parses pyproject.toml and returns [tool.pyroxide.stubs] configuration."""
    if not os.path.exists(path):
        return {}
    if tomllib is None:
        # Simple fallback parsing for simple formats if tomllib is missing
        config = {}
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            in_section = False
            for line in lines:
                line = line.strip()
                if line.startswith("[tool.pyroxide.stubs]"):
                    in_section = True
                    continue
                if line.startswith("[") and in_section:
                    break
                if in_section and "=" in line:
                    parts = line.split("=", 1)
                    key = parts[0].strip()
                    val_str = parts[1].strip()
                    # basic dict parsing if it looks like { type = "wasm", ... }
                    if val_str.startswith("{") and val_str.endswith("}"):
                        val_dict = {}
                        val_str = val_str[1:-1]
                        for pair in val_str.split(","):
                            if ":" in pair:
                                k, v = pair.split(":", 1)
                            elif "=" in pair:
                                k, v = pair.split("=", 1)
                            else:
                                continue
                            val_dict[k.strip().replace('"', "").replace("'", "")] = (
                                v.strip().replace('"', "").replace("'", "")
                            )
                        config[key] = val_dict
        except Exception:
            pass
        return config

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return data.get("tool", {}).get("pyroxide", {}).get("stubs", {})
    except Exception as e:
        print(f"Error parsing pyproject.toml: {e}", file=sys.stderr)
        return {}


def scan_py_files(target_dir: str = ".") -> Dict[str, Dict[str, str]]:
    """
    Scans Python files recursively and extracts literal registration/compilation calls:
    - compile_rust("name", "source_code")
    - compile_c("name", "source_code")
    - compile_zig("name", "source_code")
    - register_dylib("name", "path")
    - register_wasm("name", wasm_bytes)
    """
    modules = {}
    for root, _, files in os.walk(target_dir):
        # Skip virtualenvs or common build dirs
        if any(
            d in root.split(os.sep)
            for d in [".venv", "venv", ".git", "build", "dist", "target"]
        ):
            continue
        for file in files:
            if not file.endswith(".py"):
                continue
            file_path = os.path.join(root, file)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=file_path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        func_name = None
                        if isinstance(node.func, ast.Name):
                            func_name = node.func.id
                        elif isinstance(node.func, ast.Attribute):
                            func_name = node.func.attr

                        if func_name in [
                            "compile_rust",
                            "compile_c",
                            "compile_zig",
                            "register_dylib",
                            "register_wasm",
                            "register_wasm_wat",
                        ]:
                            # We need at least 2 arguments
                            if len(node.args) >= 2:
                                name_arg = node.args[0]
                                second_arg = node.args[1]
                                # Check if name is a literal string
                                if isinstance(name_arg, ast.Constant) and isinstance(
                                    name_arg.value, str
                                ):
                                    name = name_arg.value
                                    # Check if the second arg is a literal string or bytes
                                    if isinstance(second_arg, ast.Constant):
                                        val = second_arg.value
                                        if func_name == "register_dylib":
                                            modules[name] = {
                                                "type": "dylib",
                                                "path": val,
                                            }
                                        elif func_name in [
                                            "compile_rust",
                                            "compile_c",
                                            "compile_zig",
                                        ]:
                                            lang = (
                                                "rust"
                                                if func_name == "compile_rust"
                                                else func_name.split("_")[1]
                                            )
                                            modules[name] = {
                                                "type": lang,
                                                "source": val,
                                            }
                                        elif func_name == "register_wasm":
                                            # If it's a bytes literal
                                            if isinstance(val, bytes):
                                                modules[name] = {
                                                    "type": "wasm",
                                                    "bytes": val,
                                                }
                                        elif func_name == "register_wasm_wat":
                                            if isinstance(val, str):
                                                modules[name] = {
                                                    "type": "wat",
                                                    "wat": val,
                                                }
            except Exception:
                # Silently ignore parse errors on invalid files
                pass
    return modules


def run_build_stubs(args) -> int:
    config = {}

    # 1. Read pyproject.toml configuration if enabled
    if not args.no_pyproject:
        config.update(parse_pyproject(args.pyproject))

    # 2. Run AST scanner if enabled
    if args.scan:
        config.update(scan_py_files(args.scan_dir))

    # 3. Process explicit WASM command line argument
    if args.wasm:
        for item in args.wasm:
            if "=" in item:
                name, path = item.split("=", 1)
                config[name] = {"type": "wasm", "path": path}

    # 4. Process explicit dylib command line argument
    if args.dylib:
        for item in args.dylib:
            if "=" in item:
                name, path = item.split("=", 1)
                config[name] = {"type": "dylib", "path": path}

    if not config:
        print(
            "No modules configured or discovered for stub generation.", file=sys.stderr
        )
        return 0

    success_count = 0
    for name, opts in config.items():
        mtype = opts.get("type", "").lower()
        out_path = (
            os.path.join(args.out_dir, f"{name}_proxy.pyi") if args.out_dir else None
        )

        try:
            if mtype == "wasm" and "path" in opts:
                with open(opts["path"], "rb") as f:
                    wasm_bytes = f.read()
                pyroxide.register_wasm(name, wasm_bytes)
                generate_stubs(name, "wasm", out_path=out_path)
                print(
                    f"Generated Wasm stubs for '{name}' -> {out_path or name + '_proxy.pyi'}"
                )
                success_count += 1
            elif mtype == "wasm" and "bytes" in opts:
                pyroxide.register_wasm(name, opts["bytes"])
                generate_stubs(name, "wasm", out_path=out_path)
                print(
                    f"Generated Wasm stubs for '{name}' -> {out_path or name + '_proxy.pyi'}"
                )
                success_count += 1
            elif mtype == "wat" and "wat" in opts:
                pyroxide.register_wasm_wat(name, opts["wat"])
                generate_stubs(name, "wasm", out_path=out_path)
                print(
                    f"Generated Wasm WAT stubs for '{name}' -> {out_path or name + '_proxy.pyi'}"
                )
                success_count += 1
            elif mtype == "dylib" and "path" in opts:
                pyroxide.register_dylib(name, opts["path"])
                generate_stubs(name, "dylib", out_path=out_path)
                print(
                    f"Generated Dylib stubs for '{name}' -> {out_path or name + '_proxy.pyi'}"
                )
                success_count += 1
            elif mtype == "rust" and "source" in opts:
                pyroxide.compile_rust(name, opts["source"])
                generate_stubs(name, "dylib", out_path=out_path)
                print(
                    f"Compiled and generated Rust stubs for '{name}' -> {out_path or name + '_proxy.pyi'}"
                )
                success_count += 1
            elif mtype == "c" and "source" in opts:
                pyroxide.compile_c(name, opts["source"])
                generate_stubs(name, "dylib", out_path=out_path)
                print(
                    f"Compiled and generated C stubs for '{name}' -> {out_path or name + '_proxy.pyi'}"
                )
                success_count += 1
            elif mtype == "zig" and "source" in opts:
                pyroxide.compile_zig(name, opts["source"])
                generate_stubs(name, "dylib", out_path=out_path)
                print(
                    f"Compiled and generated Zig stubs for '{name}' -> {out_path or name + '_proxy.pyi'}"
                )
                success_count += 1
            else:
                print(
                    f"Skipping module '{name}': invalid config {opts}", file=sys.stderr
                )
        except Exception as e:
            print(f"Failed to generate stubs for '{name}': {e}", file=sys.stderr)

    print(f"Stub generation complete: {success_count} succeeded.")
    return 0 if success_count > 0 else 1


def main():
    parser = argparse.ArgumentParser(
        prog="pyroxide", description="Pyroxide developer command-line tool"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    stub_parser = subparsers.add_parser(
        "build-stubs", help="Statically generate stub files for WASM/dylib modules"
    )
    stub_parser.add_argument(
        "--pyproject", default="pyproject.toml", help="Path to pyproject.toml"
    )
    stub_parser.add_argument(
        "--no-pyproject",
        action="store_true",
        help="Disable reading config from pyproject.toml",
    )
    stub_parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan Python files in search directory for compile/register calls",
    )
    stub_parser.add_argument(
        "--scan-dir", default=".", help="Directory to scan recursively"
    )
    stub_parser.add_argument(
        "--wasm", action="append", help="Explicit WASM module (format: name=path)"
    )
    stub_parser.add_argument(
        "--dylib", action="append", help="Explicit dylib module (format: name=path)"
    )
    stub_parser.add_argument(
        "--out-dir",
        default=".",
        help="Directory where proxy py/pyi files will be written",
    )

    args = parser.parse_args()
    if args.command == "build-stubs":
        sys.exit(run_build_stubs(args))


if __name__ == "__main__":
    main()
