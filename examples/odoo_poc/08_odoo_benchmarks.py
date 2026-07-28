import concurrent.futures
import os

os.environ.setdefault("PYROXIDE_WORKERS", "4")
os.environ.setdefault("PYROXIDE_MAX_PROCESSES", "4")

import pyroxide

from examples.benchmarks.bench_utils import emit, measure, metadata
from examples.odoo_poc.odoo_poc_helper import (
    audit_financial_data,
    create_mock_ledger,
    process_financial_data,
    process_financial_data_isolated,
)

TASKS = 4
REPETITIONS = 5


def main() -> None:
    ledger = create_mock_ledger(20_000)

    def executor_sample(executor) -> None:
        futures = [executor.submit(audit_financial_data, ledger) for _ in range(TASKS)]
        assert all(future.result() for future in futures)

    def pyroxide_sample(task_function) -> None:
        handles = task_function.batch([ledger] * TASKS)
        assert all(handle.result() for handle in handles)

    with (
        concurrent.futures.ThreadPoolExecutor(max_workers=4) as thread_pool,
        concurrent.futures.ProcessPoolExecutor(max_workers=4) as process_pool,
    ):
        thread_pool.submit(audit_financial_data, ledger).result()
        process_pool.submit(audit_financial_data, ledger).result()
        process_financial_data(ledger).result()
        process_financial_data_isolated(ledger).result()

        results = {
            "thread_pool": measure(lambda: executor_sample(thread_pool), REPETITIONS),
            "process_pool": measure(lambda: executor_sample(process_pool), REPETITIONS),
            "pyroxide_threaded": measure(
                lambda: pyroxide_sample(process_financial_data), REPETITIONS
            ),
            "pyroxide_isolated": measure(
                lambda: pyroxide_sample(process_financial_data_isolated), REPETITIONS
            ),
        }

    emit(
        {
            "benchmark": "odoo_arrow_equivalent_python_workload",
            "metadata": metadata(),
            "configuration": {
                "tasks_per_sample": TASKS,
                "repetitions": REPETITIONS,
                "workers": 4,
                "cold_start_included": False,
                "ledger_bytes": len(ledger),
                "engine": pyroxide.stats(),
            },
            "results": results,
        },
        None,
    )


if __name__ == "__main__":
    main()
