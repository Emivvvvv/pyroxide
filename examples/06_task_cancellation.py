# -*- coding: utf-8 -*-
import time

from example_tasks import slow_isolated_task

if __name__ == "__main__":
    print("--- 6. Task Cancellation Example ---")

    # Submit a slow task
    print("Dispatching slow financial report generation...")
    h_slow = slow_isolated_task(10.0)
    time.sleep(0.5)

    # This task is isolated, so running cancellation terminates its worker.
    print("Canceling report generation...")
    cancelled = h_slow.cancel()
    print(f"Task cancelled successfully? {cancelled} | Final status: {h_slow.status}")
