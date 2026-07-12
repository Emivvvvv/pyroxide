import functools
from typing import Callable, TypeVar, Any, overload, Optional
from ._pyroxide import submit_task
from .types import TaskHandle

P = TypeVar("P")
R = TypeVar("R")


@overload
def task(func: Callable[[P], R]) -> Callable[[P], TaskHandle]: ...


@overload
def task(
    *, native: bool = ...
) -> Callable[[Callable[[P], R]], Callable[[P], TaskHandle]]: ...


def task(func: Optional[Callable[[P], R]] = None, *, native: bool = False) -> Any:
    """
    Decorator to offload a function's work to the Rust engine core.

    Usage:
        @task
        def process_data(payload: str) -> str:
            # Executed in a background thread by the Rust engine (with GIL temporarily acquired).
            return payload.upper()

        @task(native=True)
        def process_natively(payload: str) -> None:
            # Executed natively by Rust, completely GIL-free.
            pass
    """
    if func is None:
        return lambda f: task(f, native=native)

    @functools.wraps(func)
    def wrapper(payload: P, *args: Any, **kwargs: Any) -> TaskHandle:
        # If native, we pass None as the callable to trigger native execution.
        callable_to_pass = None if native else func
        task_id = submit_task(callable_to_pass, payload)
        return TaskHandle(task_id)

    def batch(payloads: list) -> list[TaskHandle]:
        """
        Submits a batch of payloads under a single write lock.
        """
        from ._pyroxide import submit_batch

        callable_to_pass = None if native else func
        task_ids = submit_batch(callable_to_pass, payloads)
        return [TaskHandle(tid) for tid in task_ids]

    wrapper.batch = batch
    return wrapper
