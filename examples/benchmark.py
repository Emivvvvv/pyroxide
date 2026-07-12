import time
import concurrent.futures
from pyroxide import task


# Decorate a dummy task.
@task
def benchmark_task(payload):
    pass


def run_single_thread(num_tasks):
    start = time.time()
    handles = []
    for i in range(num_tasks):
        handles.append(benchmark_task(f"payload_{i}"))

    # Wait for all
    for h in handles:
        h.wait()
    end = time.time()
    return end - start


def submit_and_wait(i):
    h = benchmark_task(f"payload_{i}")
    h.wait()


def run_multi_threaded(num_tasks, num_threads):
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        list(executor.map(submit_and_wait, range(num_tasks)))
    end = time.time()
    return end - start


if __name__ == "__main__":
    print("=== Pyroxide Performance Benchmark ===")

    # Run a small warm up
    benchmark_task("warmup").wait()

    print("\n1. Single Threaded Task Submission & Waiting:")
    for num_tasks in [10, 20, 50]:
        duration = run_single_thread(num_tasks)
        avg_latency = (duration / num_tasks) * 1000
        print(
            f"   Tasks: {num_tasks:4d} | Total Time: {duration:.4f}s | Avg Latency: {avg_latency:.2f}ms"
        )

    print("\n2. Multi-Threaded Concurrent Submission (Lock Contention & Polling):")
    for threads in [2, 4, 8]:
        num_tasks = 40
        duration = run_multi_threaded(num_tasks, threads)
        print(
            f"   Tasks: {num_tasks:4d} | Threads: {threads:2d} | Total Time: {duration:.4f}s"
        )
