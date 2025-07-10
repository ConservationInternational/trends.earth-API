"""EXECUTION CLEANUP TASKS"""

import contextlib
import datetime
import logging

from celery import Task
import rollbar
from sqlalchemy import and_

from gefapi import db
from gefapi.models import Execution
from gefapi.services.docker_service import get_docker_client

logger = logging.getLogger(__name__)


class ExecutionCleanupTask(Task):
    """Base task for execution cleanup"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Execution cleanup task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


@celery.task(base=ExecutionCleanupTask, bind=True)
def cleanup_stale_executions(self):
    """Clean up stale executions older than 3 days that are not FINISHED or FAILED"""
    logger.info("[TASK]: Starting stale execution cleanup")

    # Import here to get the app instance
    from gefapi import app

    with app.app_context():
        try:
            # Calculate cutoff date (3 days ago)
            cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(days=3)

            logger.info(f"[TASK]: Looking for executions started before {cutoff_date}")

            # Find stale executions that are not finished or failed
            stale_executions = (
                db.session.query(Execution)
                .filter(
                    and_(
                        Execution.start_date < cutoff_date,
                        Execution.status.notin_(["FINISHED", "FAILED"]),
                    )
                )
                .all()
            )

            logger.info(f"[TASK]: Found {len(stale_executions)} stale executions")

            if not stale_executions:
                logger.info("[TASK]: No stale executions found")
                return {"cleaned_up": 0, "docker_services_removed": 0}

            cleaned_up_count = 0
            docker_services_removed = 0

            for execution in stale_executions:
                try:
                    # Log execution details
                    logger.info(
                        "[TASK]: Processing stale execution %s (status: %s, started: %s)",
                        execution.id,
                        execution.status,
                        execution.start_date,
                    )

                    # Set execution status to FAILED
                    execution.status = "FAILED"
                    execution.end_date = datetime.datetime.utcnow()
                    execution.progress = 100

                    db.session.add(execution)

                    # Try to clean up associated Docker service
                    docker_service_name = f"execution-{execution.id}"

                    try:
                        docker_client = get_docker_client()
                        if docker_client is not None:
                            # Try to find and remove the Docker service
                            try:
                                services = docker_client.services.list(
                                    filters={"name": docker_service_name}
                                )
                                for service in services:
                                    logger.info(
                                        "[TASK]: Removing Docker service %s for execution %s",
                                        service.name,
                                        execution.id,
                                    )
                                    service.remove()
                                    docker_services_removed += 1
                            except Exception as docker_error:
                                logger.warning(
                                    "[TASK]: Failed to remove Docker service %s: %s",
                                    docker_service_name,
                                    docker_error,
                                )

                            # Also try to remove any containers with the same name
                            try:
                                containers = docker_client.containers.list(
                                    filters={"name": docker_service_name},
                                    all=True,  # Include stopped containers
                                )
                                for container in containers:
                                    logger.info(
                                        "[TASK]: Removing Docker container %s for execution %s",
                                        container.name,
                                        execution.id,
                                    )
                                    container.remove(force=True)
                                    docker_services_removed += 1
                            except Exception as docker_error:
                                logger.warning(
                                    "[TASK]: Failed to remove Docker container %s: %s",
                                    docker_service_name,
                                    docker_error,
                                )
                        else:
                            logger.warning(
                                "[TASK]: Docker client not available, skipping Docker cleanup"
                            )
                    except Exception as docker_error:
                        logger.error(
                            "[TASK]: Error accessing Docker for execution %s: %s",
                            execution.id,
                            docker_error,
                        )

                    cleaned_up_count += 1

                    logger.info(
                        f"[TASK]: Successfully cleaned up execution {execution.id}"
                    )

                except Exception as execution_error:
                    logger.error(
                        "[TASK]: Error cleaning up execution %s: %s",
                        execution.id,
                        execution_error,
                    )
                    # Continue with other executions even if one fails
                    continue

            # Commit all database changes at once
            try:
                db.session.commit()
                logger.info(
                    "[TASK]: Successfully committed %d execution updates",
                    cleaned_up_count,
                )
            except Exception as commit_error:
                logger.error(
                    f"[TASK]: Failed to commit execution updates: {commit_error}"
                )
                db.session.rollback()
                raise commit_error

            result = {
                "cleaned_up": cleaned_up_count,
                "docker_services_removed": docker_services_removed,
                "cutoff_date": cutoff_date.isoformat(),
            }

            logger.info(
                "[TASK]: Cleanup completed. Cleaned up %d executions, "
                "removed %d Docker services/containers",
                cleaned_up_count,
                docker_services_removed,
            )

            return result

        except Exception as error:
            logger.error(f"[TASK]: Error during stale execution cleanup: {str(error)}")
            logger.exception("Full traceback:")

            # Try to rollback the session
            with contextlib.suppress(Exception):
                db.session.rollback()

            # Report to rollbar if available
            with contextlib.suppress(Exception):
                rollbar.report_exc_info()

            # Re-raise the error so Celery can handle it
            raise error


@celery.task(base=ExecutionCleanupTask, bind=True)
def cleanup_finished_executions(self):
    """Clean up Docker resources for executions that finished in the past day"""
    logger.info("[TASK]: Starting finished execution cleanup")

    # Import here to get the app instance
    from gefapi import app

    with app.app_context():
        try:
            # Calculate cutoff date (1 day ago)
            cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(days=1)

            logger.info(f"[TASK]: Looking for executions finished after {cutoff_date}")

            # Find executions that finished within the past day
            finished_executions = (
                db.session.query(Execution)
                .filter(
                    and_(
                        Execution.status == "FINISHED",
                        Execution.end_date >= cutoff_date,
                        Execution.end_date.isnot(None),
                    )
                )
                .all()
            )

            logger.info(
                "[TASK]: Found %d finished executions from the past day",
                len(finished_executions),
            )

            if not finished_executions:
                logger.info("[TASK]: No finished executions found from the past day")
                return {"cleaned_up": 0, "docker_services_removed": 0}

            docker_services_removed = 0

            for execution in finished_executions:
                try:
                    # Log execution details
                    logger.info(
                        "[TASK]: Processing finished execution %s (finished: %s)",
                        execution.id,
                        execution.end_date,
                    )

                    # Try to clean up associated Docker service
                    docker_service_name = f"execution-{execution.id}"

                    try:
                        docker_client = get_docker_client()
                        if docker_client is not None:
                            # Try to find and remove the Docker service
                            try:
                                services = docker_client.services.list(
                                    filters={"name": docker_service_name}
                                )
                                for service in services:
                                    logger.info(
                                        "[TASK]: Removing Docker service %s for finished execution %s",
                                        service.name,
                                        execution.id,
                                    )
                                    service.remove()
                                    docker_services_removed += 1
                            except Exception as docker_error:
                                logger.warning(
                                    "[TASK]: Failed to remove Docker service %s: %s",
                                    docker_service_name,
                                    docker_error,
                                )

                            # Also try to remove any containers with the same name
                            try:
                                containers = docker_client.containers.list(
                                    filters={"name": docker_service_name},
                                    all=True,  # Include stopped containers
                                )
                                for container in containers:
                                    logger.info(
                                        "[TASK]: Removing Docker container %s for finished execution %s",
                                        container.name,
                                        execution.id,
                                    )
                                    container.remove(force=True)
                                    docker_services_removed += 1
                            except Exception as docker_error:
                                logger.warning(
                                    "[TASK]: Failed to remove Docker container %s: %s",
                                    docker_service_name,
                                    docker_error,
                                )
                        else:
                            logger.warning(
                                "[TASK]: Docker client not available, skipping Docker cleanup"
                            )
                    except Exception as docker_error:
                        logger.error(
                            "[TASK]: Error accessing Docker for finished execution %s: %s",
                            execution.id,
                            docker_error,
                        )

                    logger.debug(
                        f"[TASK]: Processed finished execution {execution.id}"
                    )

                except Exception as execution_error:
                    logger.error(
                        "[TASK]: Error processing finished execution %s: %s",
                        execution.id,
                        execution_error,
                    )
                    # Continue with other executions even if one fails
                    continue

            result = {
                "cleaned_up": len(finished_executions),
                "docker_services_removed": docker_services_removed,
                "cutoff_date": cutoff_date.isoformat(),
            }

            logger.info(
                "[TASK]: Finished cleanup complete. Processed %d executions, "
                "removed %d Docker services/containers",
                len(finished_executions),
                docker_services_removed,
            )

            return result

        except Exception as error:
            logger.error(
                f"[TASK]: Error during finished execution cleanup: {str(error)}"
            )
            logger.exception("Full traceback:")

            # Report to rollbar if available
            with contextlib.suppress(Exception):
                rollbar.report_exc_info()

            # Re-raise the error so Celery can handle it
            raise error


@celery.task(base=ExecutionCleanupTask, bind=True)
def cleanup_old_failed_executions(self):
    """Clean up Docker resources for failed executions older than 14 days"""
    logger.info("[TASK]: Starting old failed execution cleanup")

    # Import here to get the app instance
    from gefapi import app

    with app.app_context():
        try:
            # Calculate cutoff date (14 days ago)
            cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(days=14)

            logger.info(
                f"[TASK]: Looking for failed executions older than {cutoff_date}"
            )

            # Find failed executions older than 14 days
            old_failed_executions = (
                db.session.query(Execution)
                .filter(
                    and_(
                        Execution.status == "FAILED",
                        Execution.end_date < cutoff_date,
                        Execution.end_date.isnot(None),
                    )
                )
                .all()
            )

            logger.info(
                "[TASK]: Found %d old failed executions", len(old_failed_executions)
            )

            if not old_failed_executions:
                logger.info("[TASK]: No old failed executions found")
                return {"cleaned_up": 0, "docker_services_removed": 0}

            docker_services_removed = 0

            for execution in old_failed_executions:
                try:
                    # Log execution details
                    logger.info(
                        "[TASK]: Processing old failed execution %s (failed: %s)",
                        execution.id,
                        execution.end_date,
                    )

                    # Try to clean up associated Docker service
                    docker_service_name = f"execution-{execution.id}"

                    try:
                        docker_client = get_docker_client()
                        if docker_client is not None:
                            # Try to find and remove the Docker service
                            try:
                                services = docker_client.services.list(
                                    filters={"name": docker_service_name}
                                )
                                for service in services:
                                    logger.info(
                                        "[TASK]: Removing Docker service %s for old failed execution %s",
                                        service.name,
                                        execution.id,
                                    )
                                    service.remove()
                                    docker_services_removed += 1
                            except Exception as docker_error:
                                logger.warning(
                                    "[TASK]: Failed to remove Docker service %s: %s",
                                    docker_service_name,
                                    docker_error,
                                )

                            # Also try to remove any containers with the same name
                            try:
                                containers = docker_client.containers.list(
                                    filters={"name": docker_service_name},
                                    all=True,  # Include stopped containers
                                )
                                for container in containers:
                                    logger.info(
                                        "[TASK]: Removing Docker container %s for old failed execution %s",
                                        container.name,
                                        execution.id,
                                    )
                                    container.remove(force=True)
                                    docker_services_removed += 1
                            except Exception as docker_error:
                                logger.warning(
                                    "[TASK]: Failed to remove Docker container %s: %s",
                                    docker_service_name,
                                    docker_error,
                                )
                        else:
                            logger.warning(
                                "[TASK]: Docker client not available, skipping Docker cleanup"
                            )
                    except Exception as docker_error:
                        logger.error(
                            "[TASK]: Error accessing Docker for old failed execution %s: %s",
                            execution.id,
                            docker_error,
                        )

                    logger.debug(
                        f"[TASK]: Processed old failed execution {execution.id}"
                    )

                except Exception as execution_error:
                    logger.error(
                        "[TASK]: Error processing old failed execution %s: %s",
                        execution.id,
                        execution_error,
                    )
                    # Continue with other executions even if one fails
                    continue

            result = {
                "cleaned_up": len(old_failed_executions),
                "docker_services_removed": docker_services_removed,
                "cutoff_date": cutoff_date.isoformat(),
            }

            logger.info(
                "[TASK]: Old failed cleanup complete. Processed %d executions, "
                "removed %d Docker services/containers",
                len(old_failed_executions),
                docker_services_removed,
            )

            return result

        except Exception as error:
            logger.error(
                f"[TASK]: Error during old failed execution cleanup: {str(error)}"
            )
            logger.exception("Full traceback:")

            # Report to rollbar if available
            with contextlib.suppress(Exception):
                rollbar.report_exc_info()

            # Re-raise the error so Celery can handle it
            raise error
