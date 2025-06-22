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

from gefapi.services.docker_service import (
    DockerService,  # noqa: E402
    docker_build,
    docker_run,
)
from gefapi.services.email_service import EmailService  # noqa: E402
from gefapi.services.script_service import ScriptService  # noqa: E402
from gefapi.services.status_service import StatusService  # noqa: E402
from gefapi.services.user_service import UserService  # noqa: E402

# Import last to avoid circular dependency
from gefapi.services.execution_service import ExecutionService  # noqa:E402, isort:skip

__all__ = [
    "DockerService",
    "docker_build",
    "docker_run",
    "EmailService",
    "ScriptService",
    "StatusService",
    "UserService",
    "ExecutionService",
]
