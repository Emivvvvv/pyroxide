# -*- coding: utf-8 -*-
import os
import time
import ctypes
import pyarrow as pa
from pyroxide import task

@task
def process_financial_data(arrow_bytes: bytes) -> bytes:
    """
    Simulated ledger auditor. Reads a serialized Arrow Table,
    computes the sum of financial transactions, and returns a new Arrow Table.
    """
    try:
        reader = pa.BufferReader(arrow_bytes)
        table = pa.ipc.open_stream(reader).read_all()
        amounts = table.column("amount").to_pylist()
        total = sum(amounts)
        
        # Build response table
        res_table = pa.Table.from_pydict({"audit_total": [total]})
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, res_table.schema) as writer:
            writer.write_table(res_table)
        return bytes(sink.getvalue())
    except Exception as e:
        raise RuntimeError(f"Audit failure: {e}")

@task(isolated=True)
def crash_worker(x: int) -> int:
    """Simulates a critical crash (abrupt exit) in a native C dependency."""
    import os
    os._exit(139) # Exits immediately with SIGSEGV exit status
    return x

@task(isolated=True)
def slow_report(duration: float) -> float:
    """Simulates a long-running, blocking financial report generation."""
    time.sleep(duration)
    return duration

@task(isolated=True)
def get_worker_pid(dummy: int) -> int:
    """Returns the process ID of the background worker."""
    return os.getpid()

@task
def thread_sleep(sec: float) -> float:
    """GIL-free sleeping task to verify thread concurrency."""
    time.sleep(sec)
    return sec

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
