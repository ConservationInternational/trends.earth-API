"""TASKS MODULE"""

# Import tasks to ensure they are registered with Celery
from gefapi.tasks import (
    docker_service_monitoring,  # noqa: F401
    execution_cleanup,  # noqa: F401
    refresh_token_cleanup,  # noqa: F401
    status_monitoring,  # noqa: F401
)
