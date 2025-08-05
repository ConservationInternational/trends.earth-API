"""DOCKER COMPLETED SERVICE MONITORING TASKS"""

import datetime
import logging

from celery import Task
import rollbar

from gefapi import db
from gefapi.models import Execution, ExecutionLog
from gefapi.services.docker_service import get_docker_client

logger = logging.getLogger(__name__)


class DockerCompletedMonitoringTask(Task):
    """Base task for Docker completed service monitoring"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Docker completed monitoring task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402

# Import celery after other imports to avoid circular dependency


@celery.task(base=DockerCompletedMonitoringTask, bind=True)
def monitor_completed_docker_services(self):
    """
    Monitor Docker services for completed executions and clean them up.
    This task specifically handles the case where executions are already marked as
    FAILED or FINISHED in the database, but their Docker services are still consuming
    cluster resources because Docker Swarm doesn't automatically remove them.

    Checks executions in FAILED and FINISHED states that:
    - Are already marked as completed in the database
    - But may still have Docker services consuming cluster resources

    When a Docker service is found for these completed executions:
    1. Remove the Docker service to free cluster resources
    2. Log the cleanup action

    This addresses the issue where Docker Swarm doesn't properly free resources
    from completed execution services, causing resource exhaustion and preventing
    new executions from being scheduled.
    """
    logger.info("[TASK]: Starting Docker completed service monitoring")

    # Import here to get the app instance
    from gefapi import app

    with app.app_context():
        try:
            # Find executions that are already marked as FAILED or FINISHED
            # but may still have Docker services consuming resources
            # Focus on recent executions to avoid checking very old ones
            completed_executions = (
                db.session.query(Execution)
                .filter(Execution.status.in_(["FAILED", "FINISHED"]))
                .filter(
                    Execution.start_date
                    >= datetime.datetime.utcnow() - datetime.timedelta(hours=48)
                )
                .order_by(Execution.start_date.desc())
                .limit(200)  # Reasonable limit to prevent resource issues
                .all()
            )

            logger.info(
                f"[TASK]: Found {len(completed_executions)} completed executions "
                f"to check for lingering services"
            )

            if not completed_executions:
                logger.info("[TASK]: No completed executions found")
                return {
                    "checked": 0,
                    "completed_services_found": 0,
                    "executions_marked_finished": 0,
                    "services_removed": 0,
                }

            try:
                docker_client = get_docker_client()
                if docker_client is None:
                    logger.warning(
                        "[TASK]: Docker client not available, skipping monitoring"
                    )
                    return {
                        "checked": 0,
                        "completed_services_found": 0,
                        "executions_marked_finished": 0,
                        "services_removed": 0,
                        "error": "Docker unavailable",
                    }
            except Exception as e:
                logger.error(f"[TASK]: Failed to get Docker client: {e}")
                return {
                    "checked": 0,
                    "completed_services_found": 0,
                    "executions_marked_finished": 0,
                    "services_removed": 0,
                    "error": str(e),
                }

            checked_count = 0
            completed_services_found = 0
            services_removed = 0

            for execution in completed_executions:
                try:
                    # Docker service name follows the pattern: execution-{execution_id}
                    docker_service_name = f"execution-{execution.id}"

                    logger.debug(
                        f"[TASK]: Checking for lingering Docker service "
                        f"{docker_service_name} for execution {execution.id} "
                        f"(status: {execution.status}, ended: {execution.end_date})"
                    )

                    # Find the Docker service
                    services = docker_client.services.list(
                        filters={"name": docker_service_name}
                    )

                    checked_count += 1

                    if not services:
                        # No service found - already cleaned up, which is good
                        logger.debug(
                            f"[TASK]: No Docker service found for completed execution "
                            f"{execution.id} - already cleaned up"
                        )
                        continue

                    # Found a service for a completed execution - this is the problem!
                    service = services[0]  # Should only be one service with this name

                    logger.info(
                        f"[TASK]: Found lingering Docker service {docker_service_name} "
                        f"for execution {execution.id} (status: {execution.status}) "
                        f"- removing to free cluster resources"
                    )

                    completed_services_found += 1

                    # Remove the service that should have been cleaned up
                    try:
                        logger.info(
                            f"[TASK]: Removing lingering Docker service "
                            f"{docker_service_name} to free cluster resources"
                        )
                        service.remove()
                        services_removed += 1

                        # Add log entry to track this cleanup
                        log_entry = ExecutionLog(
                            text=(
                                f"Lingering Docker service removed by "
                                "'monitor_completed_docker_services' task "
                                f"- execution was already {execution.status} but "
                                "service was still consuming cluster resources."
                            ),
                            level="INFO",
                            execution_id=execution.id,
                        )
                        db.session.add(log_entry)

                        logger.info(
                            f"[TASK]: Successfully removed lingering Docker service "
                            f"{docker_service_name}"
                        )
                    except Exception as cleanup_error:
                        logger.warning(
                            f"[TASK]: Failed to remove Docker service "
                            f"{docker_service_name}: {cleanup_error}"
                        )

                except Exception as e:
                    logger.error(
                        f"[TASK]: Error processing execution {execution.id}: {e}"
                    )
                    rollbar.report_exc_info()
                    continue

            # Commit all changes
            db.session.commit()

            logger.info(
                f"[TASK]: Docker completed service monitoring completed. "
                f"Checked: {checked_count}, "
                f"Lingering services found: {completed_services_found}, "
                f"Services removed: {services_removed}"
            )

            return {
                "checked": checked_count,
                "completed_services_found": completed_services_found,
                "executions_marked_finished": 0,  # We don't mark executions finished
                "services_removed": services_removed,
            }

        except Exception as e:
            logger.error(f"[TASK]: Docker completed service monitoring failed: {e}")
            rollbar.report_exc_info()
            db.session.rollback()
            raise
