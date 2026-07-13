# -*- coding: utf-8 -*-
import asyncio
from pyroxide import task

@task
def calculate_square(x: int) -> int:
    return x * x

async def main():
    print("--- 3. Asyncio Integration (async/await) Example ---")
    
    # Submit task
    handle = calculate_square(15)
    
    # Await the result non-blockingly inside asyncio event loops (FastAPI, Tornado, etc.)
    res = await handle.result_async(timeout_sec=2.0)
    print(f"Async awaited result: {res}")

if __name__ == "__main__":
    asyncio.run(main())
