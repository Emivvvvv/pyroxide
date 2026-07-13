# -*- coding: utf-8 -*-
from examples.odoo_poc.odoo_poc_helper import crash_worker

if __name__ == "__main__":
    print("--- Odoo PoC: 04. Crash Safety & Process Isolation ---")
    
    # Spawn a task that exits abruptly inside the isolated subprocess worker.
    # Pyroxide must capture the socket disruption, terminate the child cleanly,
    # and propagate a clear RuntimeError to Python without crashing the main Odoo parent process.
    print("-> Dispatching task to worker that crashes...")
    try:
        crash_worker(100).result(timeout_sec=5.0)
        raise AssertionError("Crash was not intercepted!")
    except RuntimeError as e:
        print(f"-> Main process survived! Intercepted expected crash error:\n   \"{str(e).splitlines()[0]}\"")
        
    print("✔ Odoo Crash Safety & Process Isolation PASSED.")
