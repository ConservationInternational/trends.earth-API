"""TASKS MODULE"""

# Import tasks to ensure they are registered with Celery
from gefapi.tasks import status_monitoring  # noqa: F401
