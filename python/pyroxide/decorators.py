import functools
from typing import Callable, TypeVar, Any
from ._pyroxide import submit_task
from .types import TaskHandle

P = TypeVar("P")
R = TypeVar("R")


def task(func: Callable[[P], R]) -> Callable[[P], TaskHandle]:
    """
    Decorator to offload a Python function to the Rust background worker pool.

    The decorated function will be executed on a background OS thread managed
    by Pyroxide's lock-free task broker. The GIL is temporarily acquired only
    during the Python callback execution.

    Args:
        func: The Python callable to execute in the background.

    Returns:
        A wrapper that returns a TaskHandle when called.

    Example:
        >>> @task
        ... def process_data(payload: str) -> str:
        ...     return payload.upper()
        >>> handle = process_data("hello")
        >>> result = handle.result()  # "HELLO"
    """

    @functools.wraps(func)
    def wrapper(payload: P, *args: Any, **kwargs: Any) -> TaskHandle:
        task_id = submit_task(func, payload)
        return TaskHandle(task_id)

    def batch(payloads: list) -> list[TaskHandle]:
        """
        Submits a batch of payloads under a single write lock.
        """
        from ._pyroxide import submit_batch

        task_ids = submit_batch(func, payloads)
        return [TaskHandle(tid) for tid in task_ids]

    wrapper.batch = batch
    return wrapper
