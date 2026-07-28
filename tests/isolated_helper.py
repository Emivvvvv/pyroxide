import os

from pyroxide import task


@task(isolated=True)
def square_isolated(x: int) -> int:
    return x * x


def functional_square(x: int) -> int:
    return x * x


functional_square_isolated = task(functional_square, isolated=True)


@task(isolated=True)
def crash_task(dummy: int) -> int:
    os._exit(42)


@task(isolated=True)
def echo_large_payload(payload):
    return payload


@task(isolated=True)
def make_large_response(size):
    return "x" * size


@task(isolated=True)
def get_worker_pid(dummy):
    return os.getpid()


@task(isolated=True)
def delayed_worker_pid(delay_seconds):
    import time

    time.sleep(delay_seconds)
    return os.getpid()


@task(isolated=True)
def report_pid_then_sleep(payload):
    import time

    path, delay_seconds = payload
    temporary_path = f"{path}.tmp-{os.getpid()}"
    with open(temporary_path, "w", encoding="utf-8") as pid_file:
        pid_file.write(str(os.getpid()))
        pid_file.flush()
    os.replace(temporary_path, path)
    time.sleep(delay_seconds)
    return os.getpid()


@task(isolated=True)
def long_isolated_task_helper(x):
    import time

    time.sleep(0.5)
    return x


@task(isolated=True)
def large_payload_task_helper(payload):
    return payload[:100]
