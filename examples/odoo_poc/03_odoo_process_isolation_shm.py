import pyarrow as pa

from examples.odoo_poc.odoo_poc_helper import (
    create_mock_ledger,
    process_financial_data_isolated,
)

if __name__ == "__main__":
    print("--- Odoo PoC: 03. Process Isolation and Large IPC ---")
    ledger = create_mock_ledger(200_000)
    print(f"Ledger size: {len(ledger) / (1024 * 1024):.2f} MiB")

    result = process_financial_data_isolated(ledger).result(timeout_sec=15)
    table = pa.ipc.open_stream(pa.BufferReader(result)).read_all()
    assert table.column("audit_total")[0].as_py() > 0
    print("Large serialized payload completed in an isolated worker.")
    print("Shared-memory routing avoids socket transfer, not serialization or copies.")
