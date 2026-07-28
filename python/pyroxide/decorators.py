import functools
import sys
from typing import Any, Callable, TypeVar, cast

from ._pyroxide import submit_task
from .types import TaskHandle

__all__ = ["task"]

P = TypeVar("P")
R = TypeVar("R")


class _FunctionalIsolatedCallable:
    """Picklable callable for ``task(func, isolated=True)`` functional style."""

    def __init__(self, func: Callable):
        self._func = func

    def __call__(self, payload):
        return self._func(payload)


def _registered_original(func: Callable) -> bool:
    """Return whether pickle can resolve the undecorated function by reference."""
    target = sys.modules.get(func.__module__)
    if target is None:
        return False
    for component in func.__qualname__.split("."):
        if component == "<locals>" or not hasattr(target, component):
            return False
        target = getattr(target, component)
    return target is func


def task(func_or_none=None, *, isolated: bool = False):
    """
    Decorator to offload a Python function to the Rust background worker pool.

    The decorated function is executed on a background OS thread. On a
    free-threaded CPython build, in-process Python work can execute across CPU
    cores. On regular CPython, ``isolated=True`` uses a separate interpreter
    process and serialized IPC; large serialized frames may use shared memory.

    Args:
        func_or_none: The Python callable to execute.
        isolated: Set to True to run the task in an isolated worker process.
    """

    def decorator(func: Callable[[P], R]) -> Callable[[P], TaskHandle]:
        @functools.wraps(func)
        def wrapper(payload: P) -> TaskHandle:
            import os

            from .config import _local

            if os.environ.get("PYROXIDE_WORKER") == "1":
                return cast(TaskHandle, func(payload))

            target_callable: Any = func
            if isolated:
                target_callable = (
                    _FunctionalIsolatedCallable(func)
                    if _registered_original(func)
                    else wrapper
                )
            queue_time = getattr(_local, "queue_timeout_ms", None)
            task_id = submit_task(
                target_callable, payload, isolated=isolated, queue_timeout_ms=queue_time
            )
            return TaskHandle(task_id)

        def batch(payloads: list) -> list[TaskHandle]:
            import os

            from ._pyroxide import submit_batch
            from .config import _local

            if os.environ.get("PYROXIDE_WORKER") == "1":
                return [cast(TaskHandle, func(p)) for p in payloads]

            target_callable: Any = func
            if isolated:
                target_callable = (
                    _FunctionalIsolatedCallable(func)
                    if _registered_original(func)
                    else wrapper
                )
            queue_time = getattr(_local, "queue_timeout_ms", None)
            task_ids = submit_batch(
                target_callable,
                payloads,
                isolated=isolated,
                queue_timeout_ms=queue_time,
            )
            return [TaskHandle(tid) for tid in task_ids]

        setattr(wrapper, "batch", batch)
        return wrapper

    if func_or_none is None:
        return decorator
    else:
        return decorator(func_or_none)
