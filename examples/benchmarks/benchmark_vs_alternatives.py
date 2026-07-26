import sys
import time
import subprocess
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
    uint32_t val = fib(20);
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


from bench_helper import (
    python_compute_payload,
    isolated_compute_task,
    threaded_compute_task,
)


def run_freethreaded_314_bench(num_tasks: int) -> float:
    """Runs Python 3.14t free-threaded CPython via uv run if available."""
    try:
        cmd = [
            "uv", "run", "--no-project", "--python", "3.14t",
            "python3", "examples/benchmarks/run_freethreaded_314.py"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.startswith(f"RESULTS_314T:{num_tasks}:"):
                    return float(line.split(":")[2])
    except Exception:
        pass
    return 0.0


def run_loky_bench(num_tasks: int) -> float:
    """Runs loky (Joblib process pool executor)."""
    try:
        import loky
        executor = loky.get_reusable_executor(max_workers=8)
        payload = b"benchmarking_payload_data_string_123"
        start = time.time()
        futures = [executor.submit(python_compute_payload, payload) for _ in range(num_tasks)]
        [f.result() for f in futures]
        return time.time() - start
    except Exception:
        return 0.0


def run_benchmark(num_tasks):
    payload = b"benchmarking_payload_data_string_123"
    payloads = [payload for _ in range(num_tasks)]

    print(f"\n==========================================================================")
    print(f"  Comprehensive Concurrency Benchmark: {num_tasks} Tasks")
    print(f"  Host Python: {sys.version.split()[0]} (Standard CPython)")
    print(f"==========================================================================\n")

    # 1. ThreadPoolExecutor (Python Threading)
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(python_compute_payload, payload) for _ in range(num_tasks)]
        [f.result() for f in futures]
    t_threads = time.time() - start

    # 2. ProcessPoolExecutor (Multiprocessing)
    start = time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(python_compute_payload, payload) for _ in range(num_tasks)]
        [f.result() for f in futures]
    t_process = time.time() - start

    # 3. Loky (Joblib Process Pool)
    t_loky = run_loky_bench(num_tasks)

    # 4. Python 3.14t Free-Threaded (PEP 703 GIL Bypass)
    t_314t = run_freethreaded_314_bench(num_tasks)

    # 5. Pyroxide Python Callable Task (@task)
    start = time.time()
    handles = threaded_compute_task.batch(payloads)
    [h.result() for h in handles]
    t_pyroxide_py = time.time() - start

    # 6. Pyroxide Python Callable Task (@task isolated=True, Zero-Copy SHM)
    start = time.time()
    handles = isolated_compute_task.batch(payloads)
    [h.result() for h in handles]
    t_pyroxide_py_isolated = time.time() - start

    # 7. Pyroxide Dylib Task (@dylib_task, C-ABI Native)
    start = time.time()
    handles = pyroxide_dylib_task.batch(payloads)
    [h.result() for h in handles]
    t_pyroxide_dylib = time.time() - start

    print(f"1. ThreadPoolExecutor (std 8 workers)    : {t_threads:.4f}s")
    print(f"2. ProcessPoolExecutor (std 8 workers)   : {t_process:.4f}s")
    print(f"3. Loky Process Pool (8 workers)          : {t_loky:.4f}s")
    if t_314t > 0:
        print(f"4. Python 3.14t Free-Threaded (PEP 703)  : {t_314t:.4f}s")
    print(f"5. Pyroxide @task (8 threads)             : {t_pyroxide_py:.4f}s")
    print(f"6. Pyroxide @task isolated (8 SHM procs)  : {t_pyroxide_py_isolated:.4f}s")
    print(f"7. Pyroxide @dylib_task (C-ABI GIL-Free)  : {t_pyroxide_dylib:.4f}s")

    print("\n| Concurrency Strategy | Execution Time | Speedup vs std ThreadPool | Architecture Tier |")
    print("| :--- | :---: | :---: | :--- |")
    print(f"| **Pyroxide `@dylib_task` (C Native)** | **`{t_pyroxide_dylib:.4f} s`** | **{(t_threads / max(t_pyroxide_dylib, 1e-6)):.1f}x speedup** | **Native Dynamic Plugin** |")
    if t_314t > 0:
        print(f"| **Python 3.14t Free-Threaded (PEP 703)** | **`{t_314t:.4f} s`** | **{(t_threads / max(t_314t, 1e-6)):.1f}x speedup** | **Free-Threaded CPython** |")
    print(f"| **Pyroxide `@task(isolated=True)`** | **`{t_pyroxide_py_isolated:.4f} s`** | **{(t_threads / max(t_pyroxide_py_isolated, 1e-6)):.1f}x speedup** | **Zero-Copy SHM Process Pool** |")
    print(f"| **ThreadPoolExecutor (CPython 3.11)** | `{t_threads:.4f} s` | `1.0x (baseline)` | Standard Threading |")
    print(f"| **Loky Process Pool** | `{t_loky:.4f} s` | `{(t_threads / max(t_loky, 1e-6)):.2f}x` | Joblib Subprocess Pool |")
    print(f"| **ProcessPoolExecutor** | `{t_process:.4f} s` | `{(t_threads / max(t_process, 1e-6)):.2f}x` | Pickled Subprocess Pipes |")


if __name__ == "__main__":
    # Warmup all engines
    python_compute_payload(b"warmup")
    threaded_compute_task(b"warmup").wait()
    isolated_compute_task(b"warmup").wait()
    pyroxide_dylib_task(b"warmup").wait()

    run_benchmark(50)
    run_benchmark(100)
