# -*- coding: utf-8 -*-
from example_tasks import crash_worker, worker_pid

if __name__ == "__main__":
    print("--- 5. Isolated Subprocesses & Crash Containment Example ---")

    # Submit task to a separate Python interpreter process.
    # Pyroxide utilizes Named Pipes on Windows and Unix Domain Sockets on POSIX.
    h_pid = worker_pid(0)
    print(f"Worker process PID: {h_pid.result()}")

    # A worker-process crash is contained, but process isolation is not a sandbox.
    print("Spawning a worker that exits abruptly...")
    h_crash = crash_worker(100)
    try:
        h_crash.result()
    except Exception as e:
        print(f'Worker crashed! Main process safely intercepted error:\n  "{e}"')
