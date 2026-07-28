"""Import target that creates the executable Celery benchmark worker."""

from .celery_app import create_app

app = create_app()
