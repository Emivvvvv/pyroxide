import json
import math
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Callable, Optional


def measure(operation: Callable[[], None], repetitions: int) -> dict:
    samples = []
    for _ in range(repetitions):
        started = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - started)

    ordered = sorted(samples)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "samples_seconds": samples,
        "median_seconds": statistics.median(samples),
        "p95_seconds": ordered[p95_index],
    }


def metadata() -> dict:
    return {
        "python": sys.version,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "processor": platform.processor(),
    }


def emit(report: dict, output: Optional[str]) -> None:
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if output:
        Path(output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
