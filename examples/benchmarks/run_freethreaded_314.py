import sys
import time
import concurrent.futures

def python_compute(n):
    # Fibonacci 20 workload
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(20):
        # Heavy math iterations
        a, b = b, a + b
    return b

def run_bench(num_tasks):
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(python_compute, 20) for _ in range(num_tasks)]
        [f.result() for f in futures]
    elapsed = time.time() - start
    print(f"RESULTS_314T:{num_tasks}:{elapsed:.6f}")

if __name__ == "__main__":
    is_free = hasattr(sys, "_is_gil_enabled") and not sys._is_gil_enabled()
    print(f"Python 3.14t Info: version={sys.version.split()[0]} GIL_disabled={is_free}")
    run_bench(50)
    run_bench(100)
