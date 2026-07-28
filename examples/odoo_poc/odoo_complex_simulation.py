"""Correctness and failure-handling smoke test for an Arrow-style workload."""

import time

import pyarrow as pa

from examples.odoo_poc.odoo_poc_helper import (
    crash_worker,
    create_mock_ledger,
    process_financial_data,
    process_financial_data_isolated,
    slow_report,
    thread_sleep,
)


def read_total(payload: bytes) -> float:
    table = pa.ipc.open_stream(pa.BufferReader(payload)).read_all()
    return table.column("audit_total")[0].as_py()


def run() -> None:
    small = create_mock_ledger(100)
    large = create_mock_ledger(20_000)

    assert read_total(process_financial_data(small).result()) == 49_500.0
    assert read_total(process_financial_data_isolated(large).result()) > 0

    started = time.perf_counter()
    handles = [thread_sleep(0.2) for _ in range(4)]
    assert [handle.result() for handle in handles] == [0.2] * 4
    assert time.perf_counter() - started < 0.6

    try:
        crash_worker(0).result(timeout_sec=5)
    except RuntimeError:
        pass
    else:
        raise AssertionError("isolated worker crash was not reported")

    cancellable = slow_report(5)
    deadline = time.monotonic() + 2
    while cancellable.status == "Pending":
        assert time.monotonic() < deadline
        time.sleep(0.001)
    assert cancellable.cancel() is True
    assert cancellable.status == "Cancelled"

    print("Odoo-style correctness, concurrency, crash, and cancellation checks passed.")


if __name__ == "__main__":
    run()
