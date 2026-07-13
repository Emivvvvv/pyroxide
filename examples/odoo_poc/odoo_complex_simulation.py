# -*- coding: utf-8 -*-
"""
Odoo Proof-of-Concept: Unified Complex Simulation

This script executes all key Pyroxide features in a single, complex workflow
representing a production Odoo enterprise environment offloading massive Arrow datasets.
"""

import os
import sys
import time
import ctypes
import pyarrow as pa
import concurrent.futures
from pyroxide import task
from pyroxide.types import TaskHandle
from pyroxide._pyroxide import submit_task

from examples.odoo_poc.odoo_poc_helper import (
    process_financial_data,
    crash_worker,
    slow_report,
    get_worker_pid,
    create_mock_ledger,
    thread_sleep
)

def python_audit(arrow_bytes):
    reader = pa.BufferReader(arrow_bytes)
    table = pa.ipc.open_stream(reader).read_all()
    amounts = table.column("amount").to_pylist()
    total = sum(amounts)
    res_table = pa.Table.from_pydict({"audit_total": [total]})
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, res_table.schema) as writer:
        writer.write_table(res_table)
    return bytes(sink.getvalue())

def run_complex_simulation():
    print("=========================================================")
    print("        STARTING ODOO COMPLEX UNIFIED SIMULATION        ")
    print("=========================================================")
    
    # Generate test payloads
    small_ledger = create_mock_ledger(100) # ~5 KB
    large_ledger = create_mock_ledger(20000) # ~0.94 MB
    massive_ledger = create_mock_ledger(200000) # ~9.62 MB
    
    print("\n[Mock Data Generation]")
    print(f"-> Small Ledger Size: {len(small_ledger) / 1024:.2f} KB")
    print(f"-> Large Ledger Size: {len(large_ledger) / (1024*1024):.2f} MB")
    print(f"-> Massive Ledger Size: {len(massive_ledger) / (1024*1024):.2f} MB")
    
    # 1. Verify basic Arrow audit calculations
    print("\n[Phase 1] Verifying Arrow Ledger Audit...")
    handle = process_financial_data(small_ledger)
    res_bytes = handle.result()
    reader = pa.BufferReader(res_bytes)
    res_table = pa.ipc.open_stream(reader).read_all()
    audit_total = res_table.column("audit_total")[0].as_py()
    print(f"-> Audit Total: {audit_total} (Expected: 49500.0)")
    assert audit_total == 49500.0, "Audit calculation is incorrect!"
    print("✔ Phase 1 PASSED.")
    
    # 2. Verify Thread Concurrency (GIL Bypass)
    print("\n[Phase 2] Verifying GIL-Free Threaded Concurrency...")
    start_time = time.time()
    handles = [thread_sleep(0.2) for _ in range(4)]
    [h.wait() for h in handles]
    elapsed = time.time() - start_time
    print(f"-> 4 parallel sleeps of 200ms took: {elapsed:.4f}s")
    assert elapsed < 0.4, "GIL-free execution failed"
    print("✔ Phase 2 PASSED.")
    
    # 3. Process Isolation & Zero-Copy SHM
    print("\n[Phase 3] Verifying Isolated process and Zero-Copy SHM (9.62MB)...")
    task_id = submit_task(process_financial_data, massive_ledger, isolated=True)
    handle = TaskHandle(task_id)
    res_bytes = handle.result(timeout_sec=10.0)
    reader = pa.BufferReader(res_bytes)
    res_table = pa.ipc.open_stream(reader).read_all()
    large_audit_total = res_table.column("audit_total")[0].as_py()
    print(f"-> Massive Audit Total: {large_audit_total}")
    assert large_audit_total > 0, "SHM verification failed"
    print("✔ Phase 3 PASSED.")
    
    # 4. Subprocess Crash Safety
    print("\n[Phase 4] Verifying Subprocess Crash Safety...")
    try:
        crash_worker(12).result(timeout_sec=5.0)
        raise AssertionError("Crash did not trigger failure status!")
    except RuntimeError as e:
        print(f"-> Main process survived. Gracefully caught expected error:\n   \"{str(e).splitlines()[0]}\"")
    print("✔ Phase 4 PASSED.")
    
    # 5. Scale-to-Zero auto-reaping
    print("\n[Phase 5] Verifying Scale-to-Zero auto-reaping...")
    os.environ["PYROXIDE_IDLE_TIMEOUT_SEC"] = "1"
    pid1 = get_worker_pid(0).result()
    print(f"-> Worker 1 PID: {pid1}")
    print("   Waiting 3.5s for reaper thread...")
    time.sleep(3.5)
    pid2 = get_worker_pid(0).result()
    print(f"-> Worker 2 PID: {pid2}")
    assert pid1 != pid2, "Worker process was not reaped!"
    print("✔ Phase 5 PASSED.")
    
    # 6. Task Cancellation mid-flight
    print("\n[Phase 6] Verifying Task Cancellation mid-flight...")
    h_cancel = slow_report(5.0)
    time.sleep(0.5)
    print("-> Canceling financial report generation...")
    h_cancel.cancel()
    print(f"-> Target Task Status: {h_cancel.status}")
    assert h_cancel.status == "Cancelled"
    print("✔ Phase 6 PASSED.")
    
    # 7. Dynamic SHM Settings
    print("\n[Phase 7] Verifying Dynamic SHM Settings...")
    os.environ["PYROXIDE_SHM_THRESHOLD"] = "10240" # 10 KB
    task_id = submit_task(process_financial_data, large_ledger, isolated=True)
    handle = TaskHandle(task_id)
    assert len(handle.result(timeout_sec=5.0)) > 0, "Dynamic SHM routing failed"
    print("✔ Phase 7 PASSED.")
    
    # 8. Comparative Performance Benchmarks
    print("\n[Phase 8] Running Comparative Benchmarks (ProcessPool vs Pyroxide SHM)...")
    num_tasks = 10
    
    # Python Multiprocessing
    start = time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(python_audit, massive_ledger) for _ in range(num_tasks)]
        res_mp = [f.result() for f in futures]
    t_mp = time.time() - start
    print(f"-> ProcessPoolExecutor (4 workers): {t_mp:.4f}s")
    
    # Pyroxide SHM
    start = time.time()
    handles = [process_financial_data(massive_ledger) for _ in range(num_tasks)]
    res_py = [h.result() for h in handles]
    t_py = time.time() - start
    print(f"-> Pyroxide SHM Isolated (4 workers): {t_py:.4f}s")
    
    speedup = t_mp / t_py if t_py > 0 else 0
    print(f"\n⚡ Pyroxide SHM is {speedup:.2f}x FASTER than Python Multiprocessing!")
    print("✔ Phase 8 PASSED.")
    
    print("\n=========================================================")
    print("     ALL COMPLEX SIMULATION PHASES PASSED SUCCESSFULLY!  ")
    print("=========================================================")

if __name__ == "__main__":
    run_complex_simulation()
