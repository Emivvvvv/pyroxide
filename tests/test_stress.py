import concurrent.futures
from pyroxide import task
from pyroxide._pyroxide import get_slab_size


@task
def calculate_cube(x: int) -> int:
    return x * x * x


def test_high_concurrency_stress():
    """
    Simulates a heavy multi-threaded production server workload.
    Spawns 8 client threads concurrently submitting 200 tasks each (1,600 tasks total).
    Verifies that:
    1. The task broker handles high lock-contention correctly.
    2. All results are computed and retrieved accurately.
    3. The Slab memory is fully freed (no leaks).
    """
    import gc

    gc.collect()
    initial_slab = get_slab_size()

    num_threads = 8
    tasks_per_thread = 200

    def submit_and_verify(thread_idx):
        results = []
        handles = []
        for i in range(tasks_per_thread):
            val = thread_idx * 1000 + i
            handles.append((val, calculate_cube(val)))

        for val, handle in handles:
            results.append((val**3, handle.result()))
        return results

    # Execute stress test concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(submit_and_verify, i) for i in range(num_threads)]

        for f in concurrent.futures.as_completed(futures):
            res_pairs = f.result()
            for expected, actual in res_pairs:
                assert expected == actual

    # Force a GC collection to clean up any delayed TaskHandles
    import gc

    gc.collect()

    # Assert Slab size returns to initial
    assert get_slab_size() == initial_slab, (
        "Slab leaked memory after high concurrency stress test!"
    )
