# -*- coding: utf-8 -*-
from pyroxide import task

@task
def divide_numbers(data: tuple) -> float:
    a, b = data
    return a / b # Will raise ZeroDivisionError

if __name__ == "__main__":
    print("--- 4. Traceback Propagation Example ---")
    
    handle = divide_numbers((10, 0))
    try:
        handle.result()
    except Exception as e:
        # The traceback from the background thread is captured and printed inside the main thread
        print("Caught expected exception in main thread:")
        print(f"  {type(e).__name__}: {e}")
