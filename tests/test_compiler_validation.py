import pytest
import shutil
from unittest.mock import patch
from pyroxide.plugins import compile_dylib, compile_c, compile_zig


def test_cargo_missing():
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError) as exc_info:
            compile_dylib("test_missing", "fn main() {}")
        assert "Required compiler system binary 'cargo' is not found on your PATH" in str(exc_info.value)


def test_cc_missing():
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError) as exc_info:
            compile_c("test_missing", "int main() {}")
        assert "Required compiler system binary" in str(exc_info.value)


def test_zig_missing():
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError) as exc_info:
            compile_zig("test_missing", "pub fn main() void {}")
        assert "Required compiler system binary 'zig' is not found on your PATH" in str(exc_info.value)
