import os
from pyroxide import task


@task(isolated=True)
def square_isolated(x: int) -> int:
    return x * x


@task(isolated=True)
def crash_task(dummy: int) -> int:
    os._exit(42)


@task(isolated=True)
def echo_large_payload(payload):
    return payload


@task(isolated=True)
def get_worker_pid(dummy):
    return os.getpid()


@task(isolated=True)
def long_isolated_task_helper(x):
    import time

    time.sleep(0.5)
    return x


@task(isolated=True)
def large_payload_task_helper(payload):
    return payload[:100]
