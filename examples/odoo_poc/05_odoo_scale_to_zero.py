import os
import time

os.environ.setdefault("PYROXIDE_IDLE_TIMEOUT_SEC", "1")
os.environ.setdefault("PYROXIDE_MIN_WORKERS", "0")

from examples.odoo_poc.odoo_poc_helper import get_worker_pid

if __name__ == "__main__":
    print("--- Odoo PoC: 05. Lazy Worker Reaping ---")
    first_pid = get_worker_pid(0).result()
    print(f"First worker PID: {first_pid}")
    time.sleep(3.5)  # reaper checks every two seconds
    second_pid = get_worker_pid(0).result()
    print(f"Second worker PID: {second_pid}")
    assert first_pid != second_pid
    print("An idle, already-created worker was reaped; workers are not prewarmed.")
