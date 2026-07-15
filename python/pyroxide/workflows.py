# -*- coding: utf-8 -*-
from typing import List, Iterable
from pyroxide.types import TaskHandle


class TaskGroup:
    """A collection of tasks that run in parallel and can be managed as a unit."""

    def __init__(self, handles: Iterable[TaskHandle]):
        self.handles = list(handles)

    def __repr__(self) -> str:
        return f"<TaskGroup handles={self.handles} status={self.status}>"

    @property
    def status(self) -> str:
        statuses = [h.status for h in self.handles]
        if "Failed" in statuses:
            return "Failed"
        if "Cancelled" in statuses:
            return "Cancelled"
        if all(s == "Completed" for s in statuses):
            return "Completed"
        return "Running"

    def wait(self):
        """Blocks until all tasks in the group are completed."""
        for h in self.handles:
            h.wait()

    def result(self, consume: bool = True) -> List:
        """Waits for all tasks and returns their results in order."""
        return [h.result(consume=consume) for h in self.handles]

    def cancel(self) -> bool:
        """Cancels all tasks in the group. Returns True if all were successfully cancelled."""
        results = [h.cancel() for h in self.handles]
        return all(results)

    async def __aenter__(self):
        """Enters the asynchronous context manager."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Exits the asynchronous context manager.
        If an exception occurred, cancels all tasks. Otherwise, waits for all tasks to complete.
        """
        if exc_type is not None:
            self.cancel()
        
        exceptions = []
        for h in self.handles:
            try:
                await h.result_async(consume=False)
            except Exception as e:
                exceptions.append(e)
                
        if exceptions:
            raise ExceptionGroup("TaskGroup errors", exceptions)


def group(handles: Iterable[TaskHandle]) -> TaskGroup:
    """Wraps multiple task handles into a parallel TaskGroup."""
    return TaskGroup(handles)
