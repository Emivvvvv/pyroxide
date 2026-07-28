import struct

_POINTER_SIZE = struct.calcsize("P")

if _POINTER_SIZE == 8:
    _ISIZE_CODE = "q"
    _USIZE_CODE = "Q"
elif _POINTER_SIZE == 4:
    _ISIZE_CODE = "i"
    _USIZE_CODE = "I"
else:
    raise RuntimeError(
        f"Unsupported pointer width: {_POINTER_SIZE * 8} bits"
    )

FFI_STRUCT_CODES = {
    "i32": "i",
    "u32": "I",
    "i64": "q",
    "u64": "Q",
    "isize": _ISIZE_CODE,
    "usize": _USIZE_CODE,
    "f32": "f",
    "f64": "d",
}

FFI_PYTHON_TYPES = {
    "i32": "int",
    "u32": "int",
    "i64": "int",
    "u64": "int",
    "isize": "int",
    "usize": "int",
    "f32": "float",
    "f64": "float",
}

SUPPORTED_FFI_TYPES = set(FFI_STRUCT_CODES.keys())


def validate_ffi_type(name: str) -> None:
    if name not in FFI_STRUCT_CODES:
        supported_str = ", ".join(sorted(FFI_STRUCT_CODES.keys()))
        raise ValueError(
            f"Unsupported FFI type '{name}'. Supported types: {supported_str}."
        )


def build_argument_format(types: list[str]) -> str:
    for t in types:
        validate_ffi_type(t)
    return "=" + "".join(FFI_STRUCT_CODES[t] for t in types)


def build_return_format(type_name: str) -> str:
    validate_ffi_type(type_name)
    return "=" + FFI_STRUCT_CODES[type_name]
