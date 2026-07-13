# -*- coding: utf-8 -*-
import os
import time
from examples.odoo_poc.odoo_poc_helper import get_worker_pid

if __name__ == "__main__":
    print("--- Odoo PoC: 05. Scale-to-Zero Auto-Scaling ---")
    
    # Configure idle worker reaping timeout to 1 second
    os.environ["PYROXIDE_IDLE_TIMEOUT_SEC"] = "1"
    print("-> Configured PYROXIDE_IDLE_TIMEOUT_SEC = 1 second.")
    
    # Run a task to spawn a worker and check its PID
    pid1 = get_worker_pid(0).result()
    print(f"-> Worker 1 PID: {pid1}")
    
    # Wait for the Reaper thread to clean up the idle process (timeout=1s, check interval=2s)
    print("   Waiting 3.5s for reaper thread...")
    time.sleep(3.5)
    
    # Run another task. Since Worker 1 was reaped, this will spawn a fresh Worker 2 with a different PID.
    pid2 = get_worker_pid(0).result()
    print(f"-> Worker 2 PID: {pid2}")
    
    assert pid1 != pid2, "Worker process was not reaped and scaled to zero!"
    print("✔ Odoo Scale-to-Zero Auto-Scaling PASSED.")
