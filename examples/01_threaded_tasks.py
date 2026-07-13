# -*- coding: utf-8 -*-
from pyroxide import task

@task
def calculate_square(x: int) -> int:
    """Runs on background OS threads, releasing the Python GIL."""
    return x * x

if __name__ == "__main__":
    print("--- 1. Threaded Background Tasks Example ---")
    
    # Submit task non-blockingly
    handle = calculate_square(12)
    print(f"Submitted. Current status: {handle.status}")
    
    # Wait blocks natively (using Condvar signaling) and gets the result
    # We pass consume=False to prevent the task slot from being immediately evicted,
    # allowing us to query the status afterward.
    result = handle.result(consume=False)
    print(f"Result: {result} | Final status: {handle.status}")
