"""TASKS MODULE"""

# Import tasks to ensure they are registered with Celery
from gefapi.tasks import (
    execution_cleanup,  # noqa: F401
    status_monitoring,  # noqa: F401
)
