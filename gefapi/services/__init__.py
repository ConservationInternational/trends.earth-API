"""GEFAPI SERVICES MODULE"""

import logging
import sys

logger = logging.getLogger()


def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = handle_exception

from gefapi.services.batch_service import (  # noqa: E402
    batch_run,
    get_batch_job_status,
    push_params_to_s3,
    submit_pipeline,
    submit_single_job,
    terminate_batch_jobs,
)
from gefapi.services.boundaries_service import BoundariesService  # noqa: E402
from gefapi.services.docker_service import (
    DockerService,  # noqa: E402
    docker_build,
    docker_run,
)
from gefapi.services.email_service import EmailService  # noqa: E402
from gefapi.services.oauth2_service import OAuth2Service  # noqa: E402
from gefapi.services.rate_limit_event_service import RateLimitEventService  # noqa: E402
from gefapi.services.script_service import ScriptService  # noqa: E402
from gefapi.services.status_service import StatusService  # noqa: E402
from gefapi.services.user_service import UserService  # noqa: E402

# Import last to avoid circular dependency
from gefapi.services.execution_service import ExecutionService  # noqa:E402, isort:skip

__all__ = [
    "batch_run",
    "get_batch_job_status",
    "push_params_to_s3",
    "submit_pipeline",
    "submit_single_job",
    "terminate_batch_jobs",
    "BoundariesService",
    "DockerService",
    "docker_build",
    "docker_run",
    "EmailService",
    "OAuth2Service",
    "RateLimitEventService",
    "ScriptService",
    "StatusService",
    "UserService",
    "ExecutionService",
]
