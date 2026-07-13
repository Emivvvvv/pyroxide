# -*- coding: utf-8 -*-
import pyarrow as pa
from examples.odoo_poc.odoo_poc_helper import create_mock_ledger, process_financial_data

if __name__ == "__main__":
    print("--- Odoo PoC: 01. Arrow Ledger Audit ---")
    
    # Generate mock transaction ledger (100 records)
    small_ledger = create_mock_ledger(100)
    print(f"-> Generated mock ledger of size: {len(small_ledger) / 1024:.2f} KB")
    
    # Submit audit task to Pyroxide background worker thread
    handle = process_financial_data(small_ledger)
    res_bytes = handle.result()
    
    # Read the returned Arrow Table with results
    reader = pa.BufferReader(res_bytes)
    res_table = pa.ipc.open_stream(reader).read_all()
    audit_total = res_table.column("audit_total")[0].as_py()
    
    print(f"-> Audit Total amount calculated: {audit_total} (Expected: 49500.0)")
    assert audit_total == 49500.0, "Audit calculation is incorrect!"
    print("✔ Odoo Ledger Audit PASSED.")
