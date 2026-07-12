# Performance & Benchmarks

Pyroxide is optimized to keep scheduling overhead to a minimum. 

## Local Benchmark Results

Below are benchmark latency details gathered for 200 tasks (measured on CPython 3.11, Apple M1 Pro):

| Mode | Tasks | Total Time | Avg Latency | Highlights |
|---|---|---|---|---|
| **Single Threaded** | 200 | 0.0028s | 0.01ms (14 μs) | Baseline submission |
| **Batch Submission** | 200 | 0.0017s | 0.01ms (8 μs) | **Lock-free optimization (~2x speedup)** |
| **Asyncio Non-blocking** | 200 | 0.0103s | 0.05ms (50 μs) | event-loop friendly parallel awaiting |
| **Multi-Threaded** | 40 | 0.0019s | 0.04ms (47 μs) | Multi-threaded channel concurrency |

## Running the Benchmark

You can execute the performance suite locally to verify throughput on your machine:

```bash
python examples/benchmark.py
```
