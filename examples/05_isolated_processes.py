# -*- coding: utf-8 -*-
from examples.odoo_poc.odoo_poc_helper import get_worker_pid, crash_worker

if __name__ == "__main__":
    print("--- 5. Isolated Subprocesses & Crash Safety Example ---")
    
    # Submit task. It runs in a separate process, completely bypassing the Python GIL.
    # Pyroxide utilizes Named Pipes on Windows and Unix Domain Sockets on POSIX.
    h_pid = get_worker_pid(0)
    print(f"Worker process PID: {h_pid.result()}")
    
    # Verify Crash Safety: If a subprocess crashes (e.g. SIGSEGV), the main app survives
    print("Spawning a worker that exits abruptly...")
    h_crash = crash_worker(100)
    try:
        h_crash.result()
    except Exception as e:
        print(f"Worker crashed! Main process safely intercepted error:\n  \"{e}\"")
