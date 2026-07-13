# -*- coding: utf-8 -*-
import time
from examples.odoo_poc.odoo_poc_helper import thread_sleep

if __name__ == "__main__":
    print("--- Odoo PoC: 02. GIL-Free Parallel Thread Concurrency ---")
    
    # Run 4 tasks concurrently, each sleeping for 200ms
    # Since Pyroxide background threads operate outside the Python runtime,
    # they release the GIL. If GIL-free concurrency works, the total elapsed
    # time will be ~200ms instead of 800ms.
    start_time = time.time()
    handles = [thread_sleep(0.2) for _ in range(4)]
    [h.wait() for h in handles]
    elapsed = time.time() - start_time
    
    print(f"-> 4 parallel sleeps of 200ms took: {elapsed:.4f}s")
    assert elapsed < 0.4, f"GIL-free parallel execution failed! Took {elapsed:.4f}s"
    print("✔ Odoo GIL-Free Threaded Concurrency PASSED.")
