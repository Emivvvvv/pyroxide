import os
import time

import pyarrow as pa
from pyroxide import task


def audit_financial_data(arrow_bytes: bytes) -> bytes:
    """Read an Arrow ledger and return a one-row total."""
    reader = pa.BufferReader(arrow_bytes)
    table = pa.ipc.open_stream(reader).read_all()
    total = sum(table.column("amount").to_pylist())
    result = pa.Table.from_pydict({"audit_total": [total]})
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, result.schema) as writer:
        writer.write_table(result)
    return bytes(sink.getvalue())


@task
def process_financial_data(arrow_bytes: bytes) -> bytes:
    """Run the Arrow audit on an in-process background thread."""
    return audit_financial_data(arrow_bytes)


@task(isolated=True)
def process_financial_data_isolated(arrow_bytes: bytes) -> bytes:
    """Run the same Arrow audit in a separate interpreter process."""
    return audit_financial_data(arrow_bytes)


@task(isolated=True)
def crash_worker(value: int) -> int:
    """Simulate an abrupt failure inside an isolated worker."""
    os._exit(139)


@task(isolated=True)
def slow_report(duration: float) -> float:
    """Simulate a running isolated task that can be terminated."""
    time.sleep(duration)
    return duration


@task(isolated=True)
def get_worker_pid(_: int) -> int:
    return os.getpid()


@task
def thread_sleep(seconds: float) -> float:
    """Simulate blocking I/O; time.sleep releases the GIL."""
    time.sleep(seconds)
    return seconds


def create_mock_ledger(record_count: int) -> bytes:
    ids = list(range(record_count))
    amounts = [float(index * 10) for index in range(record_count)]
    descriptions = [
        f"Transaction record line #{index}" for index in range(record_count)
    ]
    table = pa.Table.from_pydict(
        {"id": ids, "amount": amounts, "description": descriptions}
    )
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return bytes(sink.getvalue())
