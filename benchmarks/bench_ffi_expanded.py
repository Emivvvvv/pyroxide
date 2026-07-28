import time
import struct
from pyroxide import load_dylib, compile_rust

def benchmark_ffi():
    rust_code = """
    #[no_mangle]
    pub extern "C" fn pyroxide_metadata() -> *const std::os::raw::c_char {
        b"identity_f64:f64|f64;identity_u64:u64|u64;add_u64:u64,u64|u64\\0".as_ptr() as *const _
    }

    #[no_mangle]
    pub extern "C" fn identity_f64(val: f64) -> f64 { val }

    #[no_mangle]
    pub extern "C" fn identity_u64(val: u64) -> u64 { val }

    #[no_mangle]
    pub extern "C" fn add_u64(a: u64, b: u64) -> u64 { a + b }
    """
    lib_path = compile_rust("ffi_bench_lib", rust_code)

    # 1. Cold Call Latency
    t0 = time.perf_counter_ns()
    proxy = load_dylib(lib_path)
    res_cold = proxy.identity_f64(3.14159).result()
    t1 = time.perf_counter_ns()
    cold_latency_us = (t1 - t0) / 1000.0

    # 2. Hot Cached Single Calls (f64 -> f64)
    iterations = 50_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        proxy.identity_f64(3.14159).result()
    t1 = time.perf_counter()
    hot_f64_duration = t1 - t0
    hot_f64_us = (hot_f64_duration / iterations) * 1e6
    f64_ops_sec = iterations / hot_f64_duration

    # 3. Hot Cached Single Calls (u64 -> u64)
    t0 = time.perf_counter()
    for _ in range(iterations):
        proxy.identity_u64(123456789).result()
    t1 = time.perf_counter()
    hot_u64_duration = t1 - t0
    hot_u64_us = (hot_u64_duration / iterations) * 1e6

    # 4. Hot Cached Binary Calls (add_u64)
    t0 = time.perf_counter()
    for _ in range(iterations):
        proxy.add_u64(100, 200).result()
    t1 = time.perf_counter()
    hot_add_duration = t1 - t0
    hot_add_us = (hot_add_duration / iterations) * 1e6

    # 5. Batch Calls (1,000 items per batch, 50 iterations)
    batch_size = 1_000
    batch_items = [(i, i + 1) for i in range(batch_size)]
    t0 = time.perf_counter()
    for _ in range(50):
        handles = proxy.add_u64.batch(batch_items)
        _ = [h.result() for h in handles]
    t1 = time.perf_counter()
    batch_duration = t1 - t0
    total_batch_calls = 50 * batch_size
    batch_call_us = (batch_duration / total_batch_calls) * 1e6
    batch_ops_sec = total_batch_calls / batch_duration

    # 6. Isolated Process Execution
    proxy_iso = load_dylib(lib_path, isolated=True)
    iso_iterations = 2_000
    t0 = time.perf_counter()
    for _ in range(iso_iterations):
        proxy_iso.identity_f64(3.14159).result()
    t1 = time.perf_counter()
    iso_duration = t1 - t0
    iso_us = (iso_duration / iso_iterations) * 1e6
    iso_ops_sec = iso_iterations / iso_duration

    print(f"=== Pyroxide FFI Microbenchmark Results ===")
    print(f"Cold First-Call Latency:  {cold_latency_us:.2f} µs")
    print(f"Hot Single f64->f64:      {hot_f64_us:.2f} µs/op ({f64_ops_sec:,.0f} ops/sec)")
    print(f"Hot Single u64->u64:      {hot_u64_us:.2f} µs/op")
    print(f"Hot Single add_u64:       {hot_add_us:.2f} µs/op")
    print(f"Batch Execution:          {batch_call_us:.2f} µs/op ({batch_ops_sec:,.0f} ops/sec)")
    print(f"Isolated IPC Execution:  {iso_us:.2f} µs/op ({iso_ops_sec:,.0f} ops/sec)")

if __name__ == "__main__":
    benchmark_ffi()
