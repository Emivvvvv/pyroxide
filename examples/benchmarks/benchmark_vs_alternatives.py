import time
import concurrent.futures
from pyroxide import task, compile_c, dylib_task

# 1. Compile C dylib on-the-fly for dynamic comparison
C_SRC = """
#include <stdint.h>
#include <stdlib.h>

// Simple Fibonacci to simulate CPU computation
uint32_t fib(uint32_t n) {
    if (n <= 1) return n;
    return fib(n - 1) + fib(n - 2);
}

uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
    // Perform Fibonacci 20 to simulate actual CPU computation
    uint32_t val = fib(20);
    
    // Echo payload back
    uint8_t* res = (uint8_t*)malloc(len);
    for (size_t i = 0; i < len; i++) {
        res[i] = ptr[i];
    }
    *out_len = len;
    return res;
}

void pyroxide_plugin_free(uint8_t* ptr, size_t len) {
    free(ptr);
}
"""

compile_c("bench_c", C_SRC)


@dylib_task("bench_c")
def pyroxide_dylib_task(payload: bytes) -> bytes:
    pass


from bench_helper import python_compute_payload

@task
def pyroxide_python_task(payload):
    return python_compute_payload(payload)




def run_benchmark(num_tasks):
    payload = b"benchmarking_payload_data_string_123"

    print(f"\n--- Running Benchmark with {num_tasks} Tasks ---")

    # ==========================================
    # 1. ThreadPoolExecutor (Python Threading)
    # ==========================================
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(python_compute_payload, payload) for _ in range(num_tasks)
        ]
        [f.result() for f in futures]
    t_threads = time.time() - start
    print(f"ThreadPoolExecutor (8 workers) : {t_threads:.4f}s")

    # ==========================================
    # 2. ProcessPoolExecutor (Multiprocessing)
    # ==========================================
    start = time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(python_compute_payload, payload) for _ in range(num_tasks)
        ]
        [f.result() for f in futures]
    t_process = time.time() - start
    print(f"ProcessPoolExecutor (8 workers): {t_process:.4f}s")

    # ==========================================
    # 3. Pyroxide Python Callable Task (@task)
    # ==========================================
    start = time.time()
    # Batch submit
    payloads = [payload for _ in range(num_tasks)]
    handles = pyroxide_python_task.batch(payloads)
    [h.result() for h in handles]
    t_pyroxide_py = time.time() - start
    print(f"Pyroxide @task (8 workers)     : {t_pyroxide_py:.4f}s")

    # ==========================================
    # 4. Pyroxide Python Callable Task (@task isolated=True)
    # ==========================================
    from pyroxide._pyroxide import submit_batch
    start = time.time()
    task_ids = submit_batch(python_compute_payload, payloads, isolated=True)
    from pyroxide.types import TaskHandle
    handles = [TaskHandle(tid) for tid in task_ids]
    [h.result() for h in handles]
    t_pyroxide_py_isolated = time.time() - start
    print(f"Pyroxide @task isolated (8 process): {t_pyroxide_py_isolated:.4f}s")

    # ==========================================
    # 5. Pyroxide Dylib Task (@dylib_task)
    # ==========================================
    start = time.time()
    handles = pyroxide_dylib_task.batch(payloads)
    [h.result() for h in handles]
    t_pyroxide_dylib = time.time() - start
    print(f"Pyroxide @dylib_task (C-ABI)       : {t_pyroxide_dylib:.4f}s")



if __name__ == "__main__":
    # Warmup
    python_compute_payload(b"warmup")
    pyroxide_python_task(b"warmup").wait()
    from pyroxide._pyroxide import submit_task
    from pyroxide.types import TaskHandle
    TaskHandle(submit_task(python_compute_payload, b"warmup", isolated=True)).wait()
    pyroxide_dylib_task(b"warmup").wait()

    run_benchmark(50)
    run_benchmark(100)
