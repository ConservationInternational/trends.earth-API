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

from gefapi.services.batch_service import (
    batch_run,
    get_batch_job_status,
    push_params_to_s3,
    submit_pipeline,
    submit_single_job,
    terminate_batch_jobs,
)
from gefapi.services.boundaries_service import BoundariesService
from gefapi.services.client_stats_service import ClientStatsService
from gefapi.services.client_tracking_service import ClientTrackingService
from gefapi.services.docker_service import (
    DockerService,
    docker_build,
    docker_run,
)
from gefapi.services.email_service import EmailService

# Import last to avoid circular dependency
from gefapi.services.execution_service import ExecutionService
from gefapi.services.news_service import NewsService
from gefapi.services.oauth2_service import OAuth2Service
from gefapi.services.openeo_service import openeo_run
from gefapi.services.rate_limit_event_service import RateLimitEventService
from gefapi.services.script_service import ScriptService
from gefapi.services.status_service import StatusService
from gefapi.services.user_service import UserService

__all__ = [
    "BoundariesService",
    "ClientStatsService",
    "ClientTrackingService",
    "DockerService",
    "EmailService",
    "ExecutionService",
    "NewsService",
    "OAuth2Service",
    "RateLimitEventService",
    "ScriptService",
    "StatusService",
    "UserService",
    "batch_run",
    "docker_build",
    "docker_run",
    "get_batch_job_status",
    "openeo_run",
    "push_params_to_s3",
    "submit_pipeline",
    "submit_single_job",
    "terminate_batch_jobs",
]
