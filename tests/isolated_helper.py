import os
from pyroxide import task

@task(isolated=True)
def square_isolated(x: int) -> int:
    return x * x

@task(isolated=True)
def crash_task(dummy: int) -> int:
    os._exit(42)
