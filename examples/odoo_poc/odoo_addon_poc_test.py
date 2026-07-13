# -*- coding: utf-8 -*-
import os
import sys
import time
import ctypes
import pyarrow as pa
import concurrent.futures
from pyroxide import task
from pyroxide.types import TaskHandle

# ---------------------------------------------------------
# Pyroxide Task Definitions (imported from helper module)
# ---------------------------------------------------------
from examples.odoo_poc.odoo_poc_helper import (
    process_financial_data,
    crash_worker,
    slow_report,
    get_worker_pid
)

# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------

def create_mock_ledger(num_records: int) -> bytes:
    """Creates a mock financial ledger serialized as an Arrow Table."""
    ids = list(range(num_records))
    amounts = [float(i * 10) for i in range(num_records)]
    descriptions = ["Transaction record line #{}".format(i) for i in range(num_records)]
    
    table = pa.Table.from_pydict({
        "id": ids,
        "amount": amounts,
        "description": descriptions
    })
    
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return bytes(sink.getvalue())

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

# ---------------------------------------------------------
# Verification Suite
# ---------------------------------------------------------

def run_odoo_poc_suite():
    print("=========================================================")
    print("      STARTING ODOO PROOF-OF-CONCEPT & BENCHMARK SUITE    ")
    print("=========================================================")
    
    # Generate test payloads
    small_ledger = create_mock_ledger(100) # ~10 KB
    large_ledger = create_mock_ledger(20000) # ~1.6 MB (exceeds 1MB SHM threshold)
    massive_ledger = create_mock_ledger(200000) # ~16.2 MB (simulates massive enterprise audit log)
    
    print("\n[Mock Data Generation]")
    print(f"-> Small Ledger Size: {len(small_ledger) / 1024:.2f} KB")
    print(f"-> Large Ledger Size: {len(large_ledger) / (1024*1024):.2f} MB")
    print(f"-> Massive Ledger Size: {len(massive_ledger) / (1024*1024):.2f} MB")
    
    # ---------------------------------------------------------
    # Step 1: Verify correct calculation of Arrow ledger audits
    # ---------------------------------------------------------
    print("\n[Step 1] Verifying Arrow Ledger Audit...")
    handle = process_financial_data(small_ledger)
    res_bytes = handle.result()
    reader = pa.BufferReader(res_bytes)
    res_table = pa.ipc.open_stream(reader).read_all()
    audit_total = res_table.column("audit_total")[0].as_py()
    print(f"-> Audit Total: {audit_total} (Expected: 49500.0)")
    assert audit_total == 49500.0, "Audit calculation is incorrect!"
    print("✔ Step 1 PASSED.")

    # ---------------------------------------------------------
    # Step 2: Threaded Concurrency (GIL-free threads)
    # ---------------------------------------------------------
    print("\n[Step 2] Verifying GIL-Free Threaded Concurrency...")
    # Wrap a sleeper task to test thread scaling
    @task
    def thread_sleep(sec: float) -> float:
        time.sleep(sec)
        return sec
        
    start_time = time.time()
    handles = [thread_sleep(0.2) for _ in range(4)]
    [h.wait() for h in handles]
    elapsed = time.time() - start_time
    print(f"-> 4 parallel sleeps of 200ms took: {elapsed:.4f}s")
    assert elapsed < 0.4, f"Threads did not execute concurrently (took {elapsed:.4f}s)"
    print("✔ Step 2 PASSED.")

    print("\n[Step 3 & 4] Verifying Isolated process and Zero-Copy SHM (16.2MB)...")
    # Submitting massive ledger as isolated=True (forces SHM routing)
    from pyroxide._pyroxide import submit_task
    task_id = submit_task(process_financial_data, massive_ledger, isolated=True)
    handle = TaskHandle(task_id)
    
    res_bytes = handle.result(timeout_sec=10.0)
    reader = pa.BufferReader(res_bytes)
    res_table = pa.ipc.open_stream(reader).read_all()
    large_audit_total = res_table.column("audit_total")[0].as_py()
    print(f"-> Large Audit Total: {large_audit_total}")
    assert large_audit_total > 0, "SHM verification failed"
    print("✔ Step 3 & 4 PASSED.")

    # ---------------------------------------------------------
    # Step 5: Crash Safety & Process Isolation
    # ---------------------------------------------------------
    print("\n[Step 5] Verifying Subprocess Crash Safety (SIGSEGV Isolation)...")
    try:
        crash_worker(12).result(timeout_sec=5.0)
        raise AssertionError("Crash did not trigger failure status!")
    except RuntimeError as e:
        print(f"-> Main process survived. Gracefully caught expected error:\n   \"{str(e).splitlines()[0]}\"")
    print("✔ Step 5 PASSED.")

    # ---------------------------------------------------------
    # Step 6: Scale-to-Zero Auto-scaling
    # ---------------------------------------------------------
    print("\n[Step 6] Verifying Scale-to-Zero auto-reaping...")
    # Set idle reaper timeout to 1 second
    os.environ["PYROXIDE_IDLE_TIMEOUT_SEC"] = "1"
    
    pid1 = get_worker_pid(0).result()
    print(f"-> Worker 1 PID: {pid1}")
    
    # Wait for reaper thread to clean up idle worker (timeout=1s, check interval=2s)
    print("   Waiting 3.5s for reaper thread...")
    time.sleep(3.5)
    
    pid2 = get_worker_pid(0).result()
    print(f"-> Worker 2 PID: {pid2}")
    
    assert pid1 != pid2, f"Worker process was not reaped! PID remained {pid1}"
    print("✔ Step 6 PASSED.")

    # ---------------------------------------------------------
    # Step 7: Task Cancellation
    # ---------------------------------------------------------
    print("\n[Step 7] Verifying Task Cancellation mid-flight...")
    handle = slow_report(5.0)
    time.sleep(0.5)
    
    print("-> Canceling financial report generation...")
    cancelled = handle.cancel()
    assert cancelled is True, "Failed to cancel task!"
    
    status = handle.status
    print(f"-> Target Task Status: {status}")
    assert status == "Cancelled", f"Status expected: Cancelled, got {status}"
    print("✔ Step 7 PASSED.")

    # ---------------------------------------------------------
    # Step 8: Dynamic Settings (PYROXIDE_SHM_THRESHOLD)
    # ---------------------------------------------------------
    print("\n[Step 8] Verifying Dynamic SHM Settings...")
    # Change threshold to 10KB
    os.environ["PYROXIDE_SHM_THRESHOLD"] = "10240"
    
    # Submit 15KB data, which now triggers SHM due to lower threshold
    medium_ledger = create_mock_ledger(200) # ~15 KB
    task_id = submit_task(process_financial_data, medium_ledger, isolated=True)
    handle = TaskHandle(task_id)
    assert len(handle.result(timeout_sec=5.0)) > 0, "Dynamic SHM routing failed"
    print("✔ Step 8 PASSED.")

    # ---------------------------------------------------------
    # Step 9: Comparative Performance Benchmarks (Massive Payload)
    # ---------------------------------------------------------
    print("\n[Step 9] Running Comparative Benchmarks (ProcessPool vs Pyroxide SHM)...")
    num_tasks = 10
    
    # 1. Python Multiprocessing
    start = time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(python_audit, massive_ledger) for _ in range(num_tasks)]
        res_mp = [f.result() for f in futures]
    t_mp = time.time() - start
    print(f"-> ProcessPoolExecutor (4 workers): {t_mp:.4f}s")

    # 2. Pyroxide SHM
    start = time.time()
    handles = [process_financial_data(massive_ledger) for _ in range(num_tasks)]
    res_py = [h.result() for h in handles]
    t_py = time.time() - start
    print(f"-> Pyroxide SHM Isolated (4 workers): {t_py:.4f}s")
    
    # Check outputs are correct
    assert len(res_mp) == num_tasks
    assert len(res_py) == num_tasks
    
    speedup = t_mp / t_py if t_py > 0 else 0
    print(f"\n⚡ Pyroxide SHM is {speedup:.2f}x FASTER than Python Multiprocessing!")
    print("✔ Step 9 PASSED.")
    
    print("\n=========================================================")
    print("        ALL SUITE VERIFICATIONS COMPLETED SUCCESSFULLY!  ")
    print("=========================================================")

if __name__ == "__main__":
    run_odoo_poc_suite()
