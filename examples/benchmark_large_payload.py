import time
import concurrent.futures
from pyroxide import task
from tests.isolated_helper import echo_large_payload

def python_echo(payload):
    return payload

def run_large_payload_benchmark(num_tasks):
    # 1.5 MB payload (triggers Pyroxide SHM routing)
    large_data = "A" * (1024 * 1024 + 500 * 1024)
    print(f"\n--- Running Large Payload Benchmark ({num_tasks} Tasks, Payload Size: 1.5 MB) ---")

    # ==========================================
    # 1. ProcessPoolExecutor (Standard Multiprocessing)
    # ==========================================
    start = time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(python_echo, large_data) for _ in range(num_tasks)]
        results = [f.result() for f in futures]
    t_process = time.time() - start
    print(f"ProcessPoolExecutor (4 workers): {t_process:.4f}s")

    # ==========================================
    # 2. Pyroxide Isolated Workers with SHM Routing
    # ==========================================
    start = time.time()
    handles = [echo_large_payload(large_data) for _ in range(num_tasks)]
    results_pyroxide = [h.result() for h in handles]
    t_pyroxide_shm = time.time() - start
    print(f"Pyroxide SHM Isolated (4 workers): {t_pyroxide_shm:.4f}s")
    
    # Sanity checks
    assert len(results) == num_tasks
    assert len(results_pyroxide) == num_tasks
    assert results_pyroxide[0] == large_data

if __name__ == "__main__":
    # Warmup
    echo_large_payload("warmup").wait()
    
    # Run with 10 tasks and 50 tasks to see the speedup under high payload volumes
    run_large_payload_benchmark(10)
    run_large_payload_benchmark(50)
