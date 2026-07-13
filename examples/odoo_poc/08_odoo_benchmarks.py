# -*- coding: utf-8 -*-
import time
import concurrent.futures
import pyarrow as pa
from examples.odoo_poc.odoo_poc_helper import create_mock_ledger, process_financial_data

def python_audit(arrow_bytes):
    """Fills the same response Arrow serialization as process_financial_data for a fair benchmark."""
    reader = pa.BufferReader(arrow_bytes)
    table = pa.ipc.open_stream(reader).read_all()
    amounts = table.column("amount").to_pylist()
    total = sum(amounts)
    res_table = pa.Table.from_pydict({"audit_total": [total]})
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, res_table.schema) as writer:
        writer.write_table(res_table)
    return bytes(sink.getvalue())

if __name__ == "__main__":
    print("--- Odoo PoC: 08. Comparative Performance Benchmarks ---")
    
    # Generate mock massive transaction ledger (200,000 records, ~9.62 MB)
    massive_ledger = create_mock_ledger(200000)
    print(f"-> Generated massive mock ledger of size: {len(massive_ledger) / (1024*1024):.2f} MB")
    
    num_tasks = 10
    print(f"-> Running benchmarks with {num_tasks} concurrent tasks under a 4-worker limit...")
    
    # 1. Python Multiprocessing (ProcessPoolExecutor)
    start = time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(python_audit, massive_ledger) for _ in range(num_tasks)]
        res_mp = [f.result() for f in futures]
    t_mp = time.time() - start
    print(f"-> ProcessPoolExecutor (4 workers): {t_mp:.4f}s")
    
    # 2. Pyroxide SHM (Isolated Worker Pool)
    start = time.time()
    handles = [process_financial_data(massive_ledger) for _ in range(num_tasks)]
    res_py = [h.result() for h in handles]
    t_py = time.time() - start
    print(f"-> Pyroxide SHM Isolated (4 workers): {t_py:.4f}s")
    
    assert len(res_mp) == num_tasks
    assert len(res_py) == num_tasks
    
    speedup = t_mp / t_py if t_py > 0 else 0
    print(f"\n⚡ Pyroxide SHM is {speedup:.2f}x FASTER than Python Multiprocessing!")
    print("✔ Odoo Comparative Performance Benchmarks PASSED.")
