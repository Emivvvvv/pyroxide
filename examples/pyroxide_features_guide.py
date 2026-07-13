# -*- coding: utf-8 -*-
"""
Pyroxide: Comprehensive Developer Features Guide

This script demonstrates every core feature of Pyroxide in a clean, copy-pasteable format.
It covers:
1. Standard Threaded Background Tasks
2. Batch Submissions
3. Asyncio Integration
4. Exception & Traceback Propagation
5. Isolated Subprocesses (GIL Bypass & Crash Safety)
6. Task Cancellation
7. Payloads & Shared Memory Routing
8. On-the-Fly Native Compilations (Rust/C/Zig)
9. WebAssembly (WASM) Sandboxed Tasks
"""

import gc
import os
import time
import asyncio
import pyarrow as pa
from pyroxide import (
    task,
    wasm_task,
    dylib_task,
    compile_c,
    register_wasm
)

# ---------------------------------------------------------
# 1. Standard Threaded Background Tasks
# ---------------------------------------------------------
print("--- 1. Threaded Background Tasks ---")

@task
def calculate_square(x: int) -> int:
    # Runs on background OS threads, releasing the Python GIL
    return x * x

# Submit and get a handle instantly (non-blocking)
handle = calculate_square(12)
print(f"Task status: {handle.status}")

# We pass consume=False to retain task data in the slab for status query afterward.
result = handle.result(consume=False)
print(f"Result: {result} | Final status: {handle.status}\n")


# ---------------------------------------------------------
# 2. Batch Task Submissions
# ---------------------------------------------------------
print("--- 2. Batch Task Submissions ---")

# .batch() submits multiple payloads under a single write lock, avoiding lock contention
payloads = [10, 20, 30, 40]
handles = calculate_square.batch(payloads)

# Read all results
results = [h.result() for h in handles]
print(f"Batch results: {results}\n")


# ---------------------------------------------------------
# 3. Asyncio Integration (async/await)
# ---------------------------------------------------------
print("--- 3. Asyncio Integration ---")

async def async_demo():
    # Submit task
    handle = calculate_square(15)
    # Await the result non-blockingly inside asyncio event loops (FastAPI, Tornado, etc.)
    res = await handle.result_async(timeout_sec=2.0)
    print(f"Async awaited result: {res}\n")

asyncio.run(async_demo())


# ---------------------------------------------------------
# 4. Exception & Traceback Propagation
# ---------------------------------------------------------
print("--- 4. Traceback Propagation ---")

@task
def divide_numbers(data: tuple) -> float:
    a, b = data
    return a / b # Will raise ZeroDivisionError

handle = divide_numbers((10, 0))
try:
    handle.result()
except Exception as e:
    # The traceback from the background thread is captured and printed inside the main thread
    print("Caught expected exception:")
    print(f"  {type(e).__name__}: {e}\n")


# ---------------------------------------------------------
# 5. Isolated Subprocesses (GIL Bypass & Crash Safety)
# ---------------------------------------------------------
print("--- 5. Isolated Subprocesses ---")

# Import task from helper module to prevent pickle namespace collision
from examples.odoo_poc_helper import get_worker_pid, crash_worker

# Submit task. It runs in a separate process, completely bypassing the Python GIL
h_pid = get_worker_pid(0)
print(f"Worker process PID: {h_pid.result()}")

# Verify Crash Safety: If a subprocess crashes (e.g. SIGSEGV), the main app survives
h_crash = crash_worker(100)
try:
    h_crash.result()
except Exception as e:
    print(f"Worker crashed! Main process safely intercepted error:\n  \"{e}\"\n")


# ---------------------------------------------------------
# 6. Task Cancellation
# ---------------------------------------------------------
print("--- 6. Task Cancellation ---")

from examples.odoo_poc_helper import slow_report

# Submit a slow task
h_slow = slow_report(10.0)
time.sleep(0.5)

# Cancel it mid-flight
cancelled = h_slow.cancel()
print(f"Task cancelled? {cancelled} | Final status: {h_slow.status}\n")


# ---------------------------------------------------------
# 7. Payloads & Shared Memory Routing
# ---------------------------------------------------------
print("--- 7. Payloads & Shared Memory Routing ---")

# For payloads >= 1MB, Pyroxide automatically routes data via Shared Memory (SHM)
# and bypasses socket serialization bottlenecks.
os.environ["PYROXIDE_SHM_THRESHOLD"] = "1048576" # 1 MB threshold

# Verify memory eviction: Slot is immediately freed when reference falls out of scope
from pyroxide._pyroxide import get_slab_size
h_temp = calculate_square(5)
print(f"Slab size: {get_slab_size()}")
del h_temp
gc.collect()
print(f"Slab size after GC: {get_slab_size()}\n")


# ---------------------------------------------------------
# 8. On-the-Fly Native Compilation (Rust/C/Zig)
# ---------------------------------------------------------
print("--- 8. Dynamic Native Compilers ---")

C_SRC = """
#include <stdint.h>
#include <stdlib.h>

uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
    uint8_t* res = (uint8_t*)malloc(len);
    for (size_t i = 0; i < len; i++) {
        res[i] = ptr[i] + 1; // Basic caesar-shift
    }
    *out_len = len;
    return res;
}

void pyroxide_plugin_free(uint8_t* ptr, size_t len) {
    free(ptr);
}
"""

# Compile and register C code on-the-fly!
compile_c("caesar_shift", C_SRC)

@dylib_task("caesar_shift")
def apply_caesar(payload: bytes) -> bytes:
    pass

# Execute native task GIL-free
print(f"Caesar output: {apply_caesar(b'abc').result()}\n")


# ---------------------------------------------------------
# 9. WebAssembly (WASM) Sandboxed Tasks
# ---------------------------------------------------------
print("--- 9. WebAssembly Sandboxing ---")

# Let's register a tiny mock WASM bytecode representing a simple addition or echo function
# (In a real scenario, read the compiled .wasm file bytes)
mock_wasm_bytes = b"\x00asm\x01\x00\x00\x00" # Minimal invalid header just to show registration API

try:
    register_wasm("math_engine", mock_wasm_bytes)
    
    @wasm_task("math_engine", "add_two")
    def run_wasm_calc(a: int, b: int) -> int:
        pass
    print("WASM Task registered successfully.")
except Exception as e:
    # Will fail on startup verification with invalid header, which is expected for this mock
    print(f"WASM registration API verified. Error (expected): {e}\n")

print("=========================================================")
print("  PYROXIDE FEATURES GUIDE EXECUTED SUCCESSFULLY!         ")
print("=========================================================")
