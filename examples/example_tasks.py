import os
import time

from pyroxide import task


@task(isolated=True)
def worker_pid(_: int) -> int:
    return os.getpid()


@task(isolated=True)
def crash_worker(_: int) -> None:
    os._exit(139)


@task(isolated=True)
def slow_isolated_task(seconds: float) -> float:
    time.sleep(seconds)
    return seconds


@task(isolated=True)
def isolated_echo(payload: bytes) -> bytes:
    return payload
