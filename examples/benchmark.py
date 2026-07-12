import time
import asyncio
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


def run_batch(num_tasks):
    start = time.time()
    payloads = [f"payload_{i}" for i in range(num_tasks)]
    # Single lock acquisition batch submit
    handles = benchmark_task.batch(payloads)
    # Wait for all
    for h in handles:
        h.wait()
    end = time.time()
    return end - start


async def run_async_await(num_tasks):
    start = time.time()
    payloads = [f"payload_{i}" for i in range(num_tasks)]
    handles = benchmark_task.batch(payloads)
    # Await results asynchronously in parallel
    await asyncio.gather(*(h.result_async() for h in handles))
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
    for num_tasks in [10, 50, 200]:
        duration = run_single_thread(num_tasks)
        avg_latency = (duration / num_tasks) * 1000
        print(
            f"   Tasks: {num_tasks:4d} | Total Time: {duration:.4f}s | Avg Latency: {avg_latency:.2f}ms"
        )

    print("\n2. Batch Task Submission & Waiting (Lock-Free Optimization):")
    for num_tasks in [10, 50, 200]:
        duration = run_batch(num_tasks)
        avg_latency = (duration / num_tasks) * 1000
        print(
            f"   Tasks: {num_tasks:4d} | Total Time: {duration:.4f}s | Avg Latency: {avg_latency:.2f}ms (Batching)"
        )

    print("\n3. Asyncio Non-Blocking Parallel Waiting (result_async):")
    for num_tasks in [10, 50, 200]:
        duration = asyncio.run(run_async_await(num_tasks))
        avg_latency = (duration / num_tasks) * 1000
        print(
            f"   Tasks: {num_tasks:4d} | Total Time: {duration:.4f}s | Avg Latency: {avg_latency:.2f}ms (Async)"
        )

    print("\n4. Multi-Threaded Concurrent Submission (Lock Contention & Polling):")
    for threads in [2, 4, 8]:
        num_tasks = 40
        duration = run_multi_threaded(num_tasks, threads)
        print(
            f"   Tasks: {num_tasks:4d} | Threads: {threads:2d} | Total Time: {duration:.4f}s"
        )
