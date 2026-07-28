from pyroxide import task
from workloads import run_workload


@task(isolated=True)
def isolated_compute_task(payload: bytes) -> bytes:
    return run_workload("python_cpu", payload)


@task
def threaded_compute_task(payload: bytes) -> bytes:
    return run_workload("python_cpu", payload)


@task(isolated=True)
def isolated_echo_task(payload: bytes) -> bytes:
    return run_workload("payload_echo", payload)
