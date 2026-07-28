import os

# Set engine environment before importing Pyroxide or modules that import it.
os.environ.setdefault("PYROXIDE_SHM_THRESHOLD", "10240")

from examples.odoo_poc.odoo_poc_helper import (
    create_mock_ledger,
    process_financial_data_isolated,
)

if __name__ == "__main__":
    print("--- Odoo PoC: 07. Startup Configuration ---")
    ledger = create_mock_ledger(2_000)
    result = process_financial_data_isolated(ledger).result(timeout_sec=5)
    assert result
    print("Configured the shared-memory threshold before engine initialization.")
