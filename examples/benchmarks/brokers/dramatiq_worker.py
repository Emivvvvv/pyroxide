"""Import target that creates the executable Dramatiq benchmark worker."""

from .dramatiq_app import create_runtime

broker, result_backend, common_workload = create_runtime()
