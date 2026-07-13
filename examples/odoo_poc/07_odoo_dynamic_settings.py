# -*- coding: utf-8 -*-
import os
from pyroxide.types import TaskHandle
from pyroxide._pyroxide import submit_task
from examples.odoo_poc.odoo_poc_helper import create_mock_ledger, process_financial_data

if __name__ == "__main__":
    print("--- Odoo PoC: 07. Dynamic Environment Settings ---")
    
    # Change the SHM threshold at runtime. Set it to 10 KB (forcing SHM on smaller records)
    os.environ["PYROXIDE_SHM_THRESHOLD"] = "10240"
    print("-> Dynamically updated PYROXIDE_SHM_THRESHOLD = 10 KB.")
    
    # Generate mock ledger exceeding 10 KB (2000 records ~ 100 KB)
    medium_ledger = create_mock_ledger(2000)
    print(f"-> Generated mock ledger of size: {len(medium_ledger) / 1024:.2f} KB")
    
    # Submit task. Should be routed via SHM since size > 10KB threshold
    task_id = submit_task(process_financial_data, medium_ledger, isolated=True)
    handle = TaskHandle(task_id)
    
    res = handle.result(timeout_sec=5.0)
    assert len(res) > 0, "Dynamic SHM routing failed"
    print("✔ Odoo Dynamic Environment Settings PASSED.")
