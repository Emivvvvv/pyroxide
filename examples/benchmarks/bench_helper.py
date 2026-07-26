from pyroxide import task


def fib_py(n):
    if n <= 1:
        return n
    return fib_py(n - 1) + fib_py(n - 2)


def python_compute_payload(payload):
    fib_py(20)
    return payload


@task(isolated=True)
def isolated_compute_task(payload):
    return python_compute_payload(payload)


@task
def threaded_compute_task(payload):
    return python_compute_payload(payload)
