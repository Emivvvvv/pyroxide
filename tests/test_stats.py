import pyroxide
import pytest
from pyroxide import task


@task
def add_task(x):
    return x + 10


@task
def failing_task(x):
    raise ValueError("Expected test failure")


def test_engine_stats_tracking():
    # Fetch initial stats
    initial_stats = pyroxide.stats()
    assert isinstance(initial_stats, dict)
    assert "worker_count" in initial_stats
    assert initial_stats["worker_count"] > 0
    assert "max_processes" in initial_stats
    assert initial_stats["max_processes"] > 0
    assert "submitted_tasks" in initial_stats
    assert "completed_tasks" in initial_stats
    assert "failed_tasks" in initial_stats
    assert "cancelled_tasks" in initial_stats

    sub_before = initial_stats["submitted_tasks"]
    comp_before = initial_stats["completed_tasks"]
    fail_before = initial_stats["failed_tasks"]

    # 1. Run successful task
    handle1 = add_task(20)
    assert handle1.result() == 30

    stats_after_success = pyroxide.stats()
    assert stats_after_success["submitted_tasks"] >= sub_before + 1
    assert stats_after_success["completed_tasks"] >= comp_before + 1

    # 2. Run failing task
    handle2 = failing_task(1)
    with pytest.raises(RuntimeError):
        handle2.result()

    stats_after_fail = pyroxide.stats()
    assert stats_after_fail["failed_tasks"] >= fail_before + 1
