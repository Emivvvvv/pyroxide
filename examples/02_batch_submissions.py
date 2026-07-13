# -*- coding: utf-8 -*-
from pyroxide import task

@task
def calculate_square(x: int) -> int:
    return x * x

if __name__ == "__main__":
    print("--- 2. Batch Task Submissions Example ---")
    
    # .batch() submits multiple payloads under a single write lock,
    # minimizing lock contention on the background broker.
    payloads = [10, 20, 30, 40]
    handles = calculate_square.batch(payloads)
    
    results = [h.result() for h in handles]
    print(f"Submitted payloads: {payloads}")
    print(f"Batch results:      {results}")
