# -*- coding: utf-8 -*-
import time
from examples.odoo_poc.odoo_poc_helper import slow_report

if __name__ == "__main__":
    print("--- Odoo PoC: 06. Mid-Flight Task Cancellation ---")
    
    # Submit slow financial ledger report task
    print("-> Dispatching slow 10.0s financial report generation...")
    handle = slow_report(10.0)
    time.sleep(0.5)
    
    # Cancel it mid-flight
    print("-> Canceling financial report generation...")
    cancelled = handle.cancel()
    print(f"-> Cancellation requested? {cancelled} | Target Task Status: {handle.status}")
    
    assert cancelled is True, "Task was not cancelled!"
    print("✔ Odoo Mid-Flight Task Cancellation PASSED.")
