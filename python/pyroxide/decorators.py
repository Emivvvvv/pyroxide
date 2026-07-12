import functools
from ._pyroxide import submit_task
from .types import TaskHandle

def task(func):
    """
    Decorator to offload a function's heavy work entirely to the Rust engine core.
    Assumes the payload passed to the function is a string.
    """
    @functools.wraps(func)
    def wrapper(payload: str, *args, **kwargs) -> TaskHandle:
        # Fire the payload straight through the PyO3 boundary into Rust
        task_id = submit_task(payload)

        # Immediately hand back a pollable handle to the caller
        return TaskHandle(task_id)

    return wrapper