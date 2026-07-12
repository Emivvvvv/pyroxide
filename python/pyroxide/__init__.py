"""
Pyroxide: A high-performance, lock-free background task broker for Python powered by Rust.

Exposes a thread-safe background execution engine for offloading operations from
the main Python interpreter. Supports both GIL-releasing native Rust execution and
panic-safe background Python callbacks.

Exports:
    - task: Decorator to submit functions to the background execution pool.
    - TaskHandle: Object returned by task submission to query status and await results.
"""

from ._pyroxide import submit_task, get_status
from .decorators import task
from .types import TaskHandle

__all__ = ["submit_task", "get_status", "task", "TaskHandle"]
