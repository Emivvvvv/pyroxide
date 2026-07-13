# -*- coding: utf-8 -*-
import os
import gc
from pyroxide import task

@task
def calculate_square(x: int) -> int:
    return x * x

if __name__ == "__main__":
    print("--- 7. Shared Memory (SHM) Routing & Eviction Example ---")
    
    # For large payloads (>= 1MB), Pyroxide automatically routes data via Shared Memory
    # and bypasses socket serialization bottlenecks.
    # Users can customize this limit using PYROXIDE_SHM_THRESHOLD (value in bytes).
    os.environ["PYROXIDE_SHM_THRESHOLD"] = "1048576" # 1 MB (default)
    print(f"Configured PYROXIDE_SHM_THRESHOLD = {os.environ['PYROXIDE_SHM_THRESHOLD']} bytes.")
    
    # Verify memory eviction: Slot is immediately freed when reference falls out of scope
    from pyroxide._pyroxide import get_slab_size
    
    h_temp = calculate_square(5)
    print(f"Slab size after task submission: {get_slab_size()}")
    
    del h_temp
    gc.collect()
    print(f"Slab size after deleting reference: {get_slab_size()} (reclaimed!)")
