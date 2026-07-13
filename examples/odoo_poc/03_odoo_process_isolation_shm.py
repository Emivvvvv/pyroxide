# -*- coding: utf-8 -*-
import pyarrow as pa
from pyroxide.types import TaskHandle
from pyroxide._pyroxide import submit_task
from examples.odoo_poc.odoo_poc_helper import create_mock_ledger, process_financial_data

if __name__ == "__main__":
    print("--- Odoo PoC: 03. Process Isolation & Zero-Copy SHM ---")
    
    # Generate mock massive transaction ledger (200,000 records, ~9.62 MB)
    massive_ledger = create_mock_ledger(200000)
    print(f"-> Generated massive mock ledger of size: {len(massive_ledger) / (1024*1024):.2f} MB")
    
    # Force isolated=True. This submits the task to a separate subprocess worker pool
    # and automatically routes the 9.62MB payload via OS Shared Memory (SHM)
    task_id = submit_task(process_financial_data, massive_ledger, isolated=True)
    handle = TaskHandle(task_id)
    
    # Wait and get results
    res_bytes = handle.result(timeout_sec=10.0)
    reader = pa.BufferReader(res_bytes)
    res_table = pa.ipc.open_stream(reader).read_all()
    audit_total = res_table.column("audit_total")[0].as_py()
    
    print(f"-> Audit Total amount calculated via SHM: {audit_total}")
    assert audit_total > 0, "SHM calculation failed"
    print("✔ Odoo Process Isolation & Zero-Copy SHM PASSED.")
