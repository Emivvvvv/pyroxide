# -*- coding: utf-8 -*-
from typing import List, Iterable
import sys
import builtins
from pyroxide.types import TaskHandle
import asyncio

ExceptionGroup = getattr(builtins, "ExceptionGroup", None)
if ExceptionGroup is None:
    # Define a fallback ExceptionGroup for Python < 3.11
    class ExceptionGroup(Exception):
        def __init__(self, message: str, exceptions: List[Exception]):
            super().__init__(message)
            self.exceptions = exceptions



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
        if exc_val is not None:
            exceptions.append(exc_val)
            
        tasks = [asyncio.create_task(h.result_async(consume=False)) for h in self.handles]
        if tasks:
            try:
                while tasks:
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                    for t in done:
                        try:
                            await t
                        except Exception as e:
                            self.cancel()
                            if exc_val is not None and isinstance(e, RuntimeError) and "task cancelled" in str(e).lower():
                                continue
                            exceptions.append(e)
                    tasks = list(pending)
            except Exception as e:
                self.cancel()
                exceptions.append(e)
                
        if exceptions:
            # Sibling task cancellation errors should be filtered out to reduce noise if there is another root exception
            has_real_exception = any(not (isinstance(e, RuntimeError) and "cancelled" in str(e).lower()) for e in exceptions)
            if has_real_exception:
                exceptions = [e for e in exceptions if not (isinstance(e, RuntimeError) and "cancelled" in str(e).lower())]
            
            if len(exceptions) == 1 and exceptions[0] == exc_val:
                return False
                
            raise ExceptionGroup("TaskGroup errors", exceptions)


def group(handles: Iterable[TaskHandle]) -> TaskGroup:
    """Wraps multiple task handles into a parallel TaskGroup."""
    return TaskGroup(handles)

