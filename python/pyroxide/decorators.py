import functools
from typing import Callable, TypeVar, Any
from ._pyroxide import submit_task
from .types import TaskHandle

P = TypeVar("P")
R = TypeVar("R")


def task(func_or_none=None, *, isolated: bool = False):
    """
    Decorator to offload a Python function to the Rust background worker pool.

    The decorated function will be executed on a background OS thread managed
    by Pyroxide's lock-free task broker. If `isolated=True` is set, the task
    is executed in a separate OS process, providing full crash safety and GIL-free
    concurrency for pure Python CPU tasks.

    Args:
        func_or_none: The Python callable to execute.
        isolated: Set to True to run the task in an isolated worker process.
    """

    def decorator(func: Callable[[P], R]) -> Callable[[P], TaskHandle]:
        @functools.wraps(func)
        def wrapper(payload: P, *args: Any, **kwargs: Any) -> TaskHandle:
            import os
            if os.environ.get("PYROXIDE_WORKER") == "1":
                return func(payload, *args, **kwargs)

            target_callable = wrapper if isolated else func
            task_id = submit_task(target_callable, payload, isolated=isolated)
            return TaskHandle(task_id)

        def batch(payloads: list) -> list[TaskHandle]:
            from ._pyroxide import submit_batch
            import os
            if os.environ.get("PYROXIDE_WORKER") == "1":
                return [func(p) for p in payloads]

            target_callable = wrapper if isolated else func
            task_ids = submit_batch(target_callable, payloads, isolated=isolated)
            return [TaskHandle(tid) for tid in task_ids]

        wrapper.batch = batch
        return wrapper

    if func_or_none is None:
        return decorator
    else:
        return decorator(func_or_none)
