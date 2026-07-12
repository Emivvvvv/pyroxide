import gc
from pyroxide import task
from pyroxide._pyroxide import get_slab_size


@task
def identity_task(x):
    return x


def test_result_consume_frees_memory():
    """
    Verifies that calling handle.result(consume=True) (the default)
    immediately frees the task slot in the Rust Slab.
    """
    gc.collect()
    initial_size = get_slab_size()

    # Submit task
    handle = identity_task("test_consume")
    assert get_slab_size() == initial_size + 1

    # Retrieve result with consume=True (default)
    res = handle.result(consume=True)
    assert res == "test_consume"

    # Verify slot is freed immediately
    assert get_slab_size() == initial_size


def test_gc_eviction_for_retained_results():
    """
    Verifies that when result(consume=False) is called, the task is kept in the slab,
    but once the Python TaskHandle is deleted/dropped, garbage collection triggers
    a destructor call that safely reclaims the slot in the Rust Slab.
    """
    gc.collect()
    initial_size = get_slab_size()

    # Submit task
    handle = identity_task("test_gc")
    assert get_slab_size() == initial_size + 1

    # Retrieve result with consume=False
    res = handle.result(consume=False)
    assert res == "test_gc"

    # Slab size remains + 1
    assert get_slab_size() == initial_size + 1

    # Delete the Python handle reference and force garbage collection
    del handle
    gc.collect()

    # Slab size must return to initial size
    assert get_slab_size() == initial_size
