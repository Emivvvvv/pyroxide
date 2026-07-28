import sys

from pyroxide.config import is_free_threaded


def test_is_free_threaded_helper():
    status = is_free_threaded()
    assert isinstance(status, bool)
    if hasattr(sys, "_is_gil_enabled"):
        assert status == (not sys._is_gil_enabled())
    else:
        assert status is False
