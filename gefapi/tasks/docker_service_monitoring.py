"""DOCKER SERVICE MONITORING TASKS"""

import datetime
import logging

from celery import Task
import rollbar

from gefapi import db
from gefapi.models import Execution, ExecutionLog
from gefapi.services.docker_service import get_docker_client

logger = logging.getLogger(__name__)


class DockerServiceMonitoringTask(Task):
    """Base task for Docker service monitoring"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Docker service monitoring task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


def _check_service_failed(service):
    """
    Check if a Docker service has failed by examining its tasks.

    Args:
        service: Docker service object

    Returns:
        bool: True if service has failed (all tasks failed and exhausted retries)
    """
    try:
        tasks = service.tasks()

        if not tasks:
            # No tasks means service hasn't started properly
            return True

        # Get recent tasks (within last 10 minutes)
        recent_cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=10)

        active_tasks = []
        failed_tasks = []

        for task in tasks:
            task_status = task.get("Status", {})
            task_state = task_status.get("State", "")
            desired_state = task.get("DesiredState", "")

            # Parse timestamp
            timestamp_str = task_status.get("Timestamp", "")
            try:
                if timestamp_str:
                    # Remove timezone info for simpler parsing
                    timestamp_str = timestamp_str.replace("Z", "+00:00")
                    task_time = datetime.datetime.fromisoformat(
                        timestamp_str.replace("Z", "+00:00")
                    )

                    # Only consider recent tasks
                    if task_time < recent_cutoff:
                        continue
            except (ValueError, TypeError):
                # If we can't parse timestamp, include the task anyway
                pass

            if desired_state == "running":
                if task_state in ["running", "starting", "pending"]:
                    active_tasks.append(task)
                elif task_state in ["failed", "rejected", "shutdown"]:
                    failed_tasks.append(task)

        # Service is considered failed if:
        # 1. No active tasks AND
        # 2. Recent tasks have failed AND
        # 3. Service has attempted retries (multiple failed tasks)
        if not active_tasks and failed_tasks and len(failed_tasks) >= 2:
            logger.info(
                f"Service {service.name} considered failed: "
                f"{len(active_tasks)} active tasks, {len(failed_tasks)} failed tasks"
            )
            return True

        return False

    except Exception as e:
        logger.error(f"Error checking service {service.name} status: {e}")
        # If we can't determine status, assume it's not failed to avoid false positives
        return False


@celery.task(base=DockerServiceMonitoringTask, bind=True)
def monitor_failed_docker_services(self):
    """
    Monitor Docker services for failed executions and mark them as failed.
    Runs every 10 minutes to check pending/running executions.
    """
    logger.info("[TASK]: Starting Docker service monitoring")

    # Import here to get the app instance
    from gefapi import app

    with app.app_context():
        try:
            # Find executions in PENDING or RUNNING state
            active_executions = (
                db.session.query(Execution)
                .filter(Execution.status.in_(["PENDING", "RUNNING"]))
                .all()
            )

            logger.info(
                f"[TASK]: Found {len(active_executions)} active executions to check"
            )

            if not active_executions:
                logger.info("[TASK]: No active executions found")
                return {
                    "checked": 0,
                    "failed_services_found": 0,
                    "executions_marked_failed": 0,
                }

            try:
                docker_client = get_docker_client()
                if docker_client is None:
                    logger.warning(
                        "[TASK]: Docker client not available, skipping monitoring"
                    )
                    return {
                        "checked": 0,
                        "failed_services_found": 0,
                        "executions_marked_failed": 0,
                        "error": "Docker unavailable",
                    }
            except Exception as e:
                logger.error(f"[TASK]: Failed to get Docker client: {e}")
                return {
                    "checked": 0,
                    "failed_services_found": 0,
                    "executions_marked_failed": 0,
                    "error": str(e),
                }

            checked_count = 0
            failed_services_found = 0
            executions_marked_failed = 0

            for execution in active_executions:
                try:
                    # Docker service name follows the pattern: execution-{execution_id}
                    docker_service_name = f"execution-{execution.id}"

                    logger.debug(
                        f"[TASK]: Checking Docker service {docker_service_name} for execution {execution.id}"
                    )

                    # Find the Docker service
                    services = docker_client.services.list(
                        filters={"name": docker_service_name}
                    )

                    checked_count += 1

                    if not services:
                        # Service doesn't exist - this could mean it was never created or already removed
                        logger.info(
                            f"[TASK]: No Docker service found for execution {execution.id}, marking as failed"
                        )

                        # Mark execution as failed
                        execution.status = "FAILED"
                        execution.end_date = datetime.datetime.utcnow()
                        execution.progress = 100

                        # Add log entry
                        log_entry = ExecutionLog(
                            text="Cancelled by celery short-term cleanup task.",
                            level="ERROR",
                            execution_id=execution.id,
                        )

                        db.session.add(execution)
                        db.session.add(log_entry)

                        executions_marked_failed += 1
                        failed_services_found += 1

                    else:
                        # Check if the service has failed
                        service = services[
                            0
                        ]  # Should only be one service with this name

                        if _check_service_failed(service):
                            logger.info(
                                f"[TASK]: Docker service {docker_service_name} has failed, marking execution {execution.id} as failed"
                            )

                            # Mark execution as failed
                            execution.status = "FAILED"
                            execution.end_date = datetime.datetime.utcnow()
                            execution.progress = 100

                            # Add log entry
                            log_entry = ExecutionLog(
                                text="Cancelled by celery short-term cleanup task.",
                                level="ERROR",
                                execution_id=execution.id,
                            )

                            db.session.add(execution)
                            db.session.add(log_entry)

                            executions_marked_failed += 1
                            failed_services_found += 1

                            # Optionally clean up the failed service
                            try:
                                logger.info(
                                    f"[TASK]: Removing failed Docker service {docker_service_name}"
                                )
                                service.remove()
                            except Exception as cleanup_error:
                                logger.warning(
                                    f"[TASK]: Failed to remove Docker service {docker_service_name}: {cleanup_error}"
                                )
                        else:
                            logger.debug(
                                f"[TASK]: Docker service {docker_service_name} is still healthy"
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
                f"[TASK]: Docker service monitoring completed. "
                f"Checked: {checked_count}, Failed services: {failed_services_found}, "
                f"Executions marked failed: {executions_marked_failed}"
            )

            return {
                "checked": checked_count,
                "failed_services_found": failed_services_found,
                "executions_marked_failed": executions_marked_failed,
            }

        except Exception as e:
            logger.error(f"[TASK]: Docker service monitoring failed: {e}")
            rollbar.report_exc_info()
            db.session.rollback()
            raise
