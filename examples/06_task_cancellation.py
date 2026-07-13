# -*- coding: utf-8 -*-
import time
from examples.odoo_poc.odoo_poc_helper import slow_report

if __name__ == "__main__":
    print("--- 6. Task Cancellation Example ---")
    
    # Submit a slow task
    print("Dispatching slow financial report generation...")
    h_slow = slow_report(10.0)
    time.sleep(0.5)
    
    # Cancel it mid-flight
    print("Canceling report generation...")
    cancelled = h_slow.cancel()
    print(f"Task cancelled successfully? {cancelled} | Final status: {h_slow.status}")
