# -*- coding: utf-8 -*-
from pyroxide import group, task


@task
def calculate_square(x: int) -> int:
    return x * x


if __name__ == "__main__":
    print("--- 2. Batch Task Submissions & Groups Example ---")

    # .batch() reserves capacity for the complete input or rejects all of it.
    payloads = [10, 20, 30, 40]
    handles = calculate_square.batch(payloads)

    # 2. group() bundles handles into a TaskGroup to manage them as a unit
    tg = group(handles)
    print(f"TaskGroup status during execution: {tg.status}")

    # Await and retrieve all results (pass consume=False to retain status metadata for checking afterward)
    results = tg.result(consume=False)
    print(f"Submitted payloads: {payloads}")
    print(f"Group results:      {results}")
    print(f"Final TaskGroup status: {tg.status}")
    for handle in tg.handles:
        handle.close()
