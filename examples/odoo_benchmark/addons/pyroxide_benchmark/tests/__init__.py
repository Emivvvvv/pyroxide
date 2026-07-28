try:
    from . import test_correctness as test_correctness
    from . import test_performance as test_performance
    from . import test_transactions as test_transactions
except ModuleNotFoundError as error:
    if error.name != "odoo":
        raise
