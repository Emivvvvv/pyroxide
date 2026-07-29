"""Unix async-waker registration and event-loop completion routing."""

import asyncio
import os
import sys
import threading
from typing import Optional

from ._pyroxide import get_status

# Global variables for async waker
_waker_r: Optional[int] = None
_waker_w: Optional[int] = None
_pending_futures: dict[int, asyncio.Future] = {}
_async_waiters: set[int] = set()
_waker_thread: Optional[threading.Thread] = None
_pending_futures_lock = threading.Lock()
_waker_init_lock = threading.Lock()
_waker_atexit_registered = False


def _resolve_future_safe(task_id: int) -> None:
    with _pending_futures_lock:
        fut = _pending_futures.get(task_id)
    if fut is not None and not fut.done():
        try:
            current_status = get_status(task_id)
            if current_status in ("Completed", "Failed", "Cancelled"):
                with _pending_futures_lock:
                    if _pending_futures.get(task_id) is fut:
                        _pending_futures.pop(task_id, None)
                fut.set_result(current_status)
        except Exception as e:
            with _pending_futures_lock:
                if _pending_futures.get(task_id) is fut:
                    _pending_futures.pop(task_id, None)
            fut.set_exception(e)


def _waker_thread_loop() -> None:
    global _waker_r
    while True:
        try:
            read_fd = _waker_r
            if read_fd is None:
                break
            data = os.read(read_fd, 4096)
            if not data:
                break
            with _pending_futures_lock:
                pending = list(_pending_futures.items())
            for task_id, fut in pending:
                try:
                    loop = fut.get_loop()
                    if not loop.is_closed():
                        loop.call_soon_threadsafe(_resolve_future_safe, task_id)
                except Exception:
                    pass
        except Exception:
            if _waker_r is None:
                break


def _cleanup_waker():
    global _waker_r, _waker_w, _waker_thread
    with _pending_futures_lock:
        pending = list(_pending_futures.items())
    for task_id, fut in pending:
        try:
            loop = fut.get_loop()
            if not loop.is_closed():
                loop.call_soon_threadsafe(_resolve_future_safe, task_id)
        except Exception:
            pass

    with _waker_init_lock:
        w_fd = _waker_w
        r_fd = _waker_r
        waker_thread = _waker_thread
        _waker_w = None
        _waker_r = None
        _waker_thread = None

    if w_fd is not None:
        try:
            from ._pyroxide import unregister_async_waker

            unregister_async_waker(w_fd)
        except Exception:
            pass
        try:
            os.close(w_fd)
        except Exception:
            pass
    if r_fd is not None:
        try:
            os.close(r_fd)
        except Exception:
            pass
    if waker_thread is not None and waker_thread is not threading.current_thread():
        try:
            waker_thread.join(timeout=0.1)
        except Exception:
            pass


def ensure_waker_registered(loop: asyncio.AbstractEventLoop) -> None:
    global _waker_r, _waker_w, _waker_thread, _waker_atexit_registered
    if sys.platform == "win32":
        return

    with _waker_init_lock:
        if _waker_thread is not None and _waker_thread.is_alive():
            return

        read_fd: Optional[int] = None
        write_fd: Optional[int] = None
        try:
            from ._pyroxide import register_async_waker

            read_fd, write_fd = os.pipe()
            os.set_blocking(write_fd, False)
            _waker_r = read_fd
            _waker_w = write_fd
            register_async_waker(write_fd)

            _waker_thread = threading.Thread(target=_waker_thread_loop, daemon=True)
            _waker_thread.start()

            if not _waker_atexit_registered:
                import atexit

                atexit.register(_cleanup_waker)
                _waker_atexit_registered = True
        except Exception:
            _waker_r = None
            _waker_w = None
            _waker_thread = None
            if write_fd is not None:
                try:
                    os.close(write_fd)
                except Exception:
                    pass
            if read_fd is not None:
                try:
                    os.close(read_fd)
                except Exception:
                    pass
            raise


cleanup_waker = _cleanup_waker
