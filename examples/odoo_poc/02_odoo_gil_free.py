# -*- coding: utf-8 -*-
import time

from examples.odoo_poc.odoo_poc_helper import thread_sleep

if __name__ == "__main__":
    print("--- Odoo PoC: 02. Blocking-I/O Thread Concurrency ---")

    # Run 4 tasks concurrently, each sleeping for 200ms
    # time.sleep releases the GIL, so the waits can overlap on regular CPython.
    start_time = time.time()
    handles = [thread_sleep(0.2) for _ in range(4)]
    [h.wait() for h in handles]
    elapsed = time.time() - start_time

    print(f"-> 4 parallel sleeps of 200ms took: {elapsed:.4f}s")
    assert elapsed < 0.6, f"Blocking waits did not overlap: {elapsed:.4f}s"
    print("✔ Odoo blocking-I/O concurrency PASSED.")
