import time
import loky
from bench_helper import python_compute_payload

def run_loky(num_tasks):
    payload = b"benchmarking_payload_data_string_123"
    executor = loky.get_reusable_executor(max_workers=8)
    start = time.time()
    futures = [executor.submit(python_compute_payload, payload) for _ in range(num_tasks)]
    [f.result() for f in futures]
    elapsed = time.time() - start
    print(f"RESULTS_LOKY:{num_tasks}:{elapsed:.6f}")

if __name__ == "__main__":
    # Warmup
    loky.get_reusable_executor(max_workers=8).submit(python_compute_payload, b"warmup").result()
    run_loky(50)
    run_loky(100)
