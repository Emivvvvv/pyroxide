# -*- coding: utf-8 -*-
import time

from examples.odoo_poc.odoo_poc_helper import slow_report

if __name__ == "__main__":
    print("--- Odoo PoC: 06. Isolated Task Cancellation ---")

    # Submit slow financial ledger report task
    print("-> Dispatching slow 10.0s financial report generation...")
    handle = slow_report(10.0)
    time.sleep(0.5)

    # Running isolated work is cancelled by terminating its worker process.
    print("-> Canceling financial report generation...")
    cancelled = handle.cancel()
    print(
        f"-> Cancellation requested? {cancelled} | Target Task Status: {handle.status}"
    )

    assert cancelled is True, "Task was not cancelled!"
    print("✔ Odoo isolated cancellation PASSED.")
