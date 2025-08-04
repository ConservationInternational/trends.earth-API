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
        bool: True if service is considered failed, False otherwise
    """
    try:
        tasks = service.tasks()

        # Count task states - simple and reliable
        active_tasks = 0
        failed_tasks = 0

        for task in tasks:
            task_status = task.get("Status", {})
            task_state = task_status.get("State", "").lower()
            desired_state = task.get("DesiredState", "").lower()

            # Only count tasks that should be running
            if desired_state == "running":
                if task_state in ["running", "starting", "pending"]:
                    active_tasks += 1
                elif task_state in ["failed", "rejected", "shutdown"]:
                    failed_tasks += 1

        logger.debug(
            f"Service {service.name}: {active_tasks} active, "
            f"{failed_tasks} failed tasks"
        )

        # Specific debug for the problematic execution
        if "2e9a613c-cb54-4ced-8ad5-aec689577945" in service.name:
            logger.warning(
                f"RESTART LOOP DEBUG for execution "
                f"2e9a613c-cb54-4ced-8ad5-aec689577945: "
                f"Service {service.name}, {active_tasks} active, {failed_tasks} failed"
            )

        # Simple failure detection rules:

        # 1. No active tasks but has failed tasks = service failed
        if active_tasks == 0 and failed_tasks > 0:
            logger.warning(
                f"Service {service.name} failed: no active tasks, {failed_tasks} failed"
            )
            return True

        # 2. Multiple failed tasks = restart loop
        if failed_tasks >= 2:
            logger.warning(
                f"Service {service.name} in restart loop: {failed_tasks} failed tasks"
            )
            return True

        # 3. Active task with failed tasks = potential restart loop
        if active_tasks > 0 and failed_tasks > 0:
            logger.warning(
                f"Service {service.name} potential restart loop: "
                f"{active_tasks} active, {failed_tasks} failed"
            )
            return True

        return False

    except Exception as e:
        logger.error(f"Error checking service {service.name} status: {e}")
        return False


@celery.task(base=DockerServiceMonitoringTask, bind=True)
def monitor_failed_docker_services(self):
    """
    Monitor Docker services for failed executions and mark them as failed.
    Runs every 2 minutes to detect restart loops and failed executions.

    Checks executions in PENDING, READY, RUNNING, or FAILED states because:
    - READY: Executions that just started (Docker service created)
    - RUNNING: Executions that are currently running
    - FAILED: Executions that failed but may be restarting due to restart policy
    - PENDING: Executions that are queued to start

    Optimizations:
    - Limits to 50 most recent executions within 24 hours to reduce memory usage
    - Filters by start_date to avoid checking very old executions
    """
    logger.info("[TASK]: Starting Docker service monitoring")

    # Import here to get the app instance
    from gefapi import app

    with app.app_context():
        try:
            # Find executions in PENDING, READY, RUNNING, or FAILED state
            # READY: Executions that just started (Docker service created)
            # RUNNING: Executions that are currently running
            # FAILED: Executions that failed but may be restarting due to restart policy
            # PENDING: Executions that are queued to start
            # Limit to recent executions to reduce memory usage and processing time
            active_executions = (
                db.session.query(Execution)
                .filter(Execution.status.in_(["PENDING", "READY", "RUNNING", "FAILED"]))
                .filter(
                    Execution.start_date
                    >= datetime.datetime.utcnow() - datetime.timedelta(hours=24)
                )
                .order_by(Execution.start_date.desc())
                .limit(
                    50
                )  # Limit to 50 most recent executions to prevent memory issues
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
                        f"[TASK]: Checking Docker service {docker_service_name} "
                        f"for execution {execution.id} (status: {execution.status}, "
                        f"started: {execution.start_date})"
                    )

                    # Find the Docker service
                    services = docker_client.services.list(
                        filters={"name": docker_service_name}
                    )

                    checked_count += 1

                    if not services:
                        # Service doesn't exist - this could mean it was never
                        # created or already removed
                        logger.info(
                            f"[TASK]: No Docker service found for execution "
                            f"{execution.id}, marking as failed"
                        )

                        # Mark execution as failed
                        execution.status = "FAILED"
                        execution.end_date = datetime.datetime.utcnow()
                        execution.progress = 100

                        # Add log entry
                        log_entry = ExecutionLog(
                            text=(
                                "Cancelled by celery task "
                                "'monitor_failed_docker_services' "
                                "- Docker service not found."
                            ),
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
                                f"[TASK]: Docker service {docker_service_name} "
                                f"has failed, marking execution {execution.id} "
                                f"as failed"
                            )

                            # Mark execution as failed
                            execution.status = "FAILED"
                            execution.end_date = datetime.datetime.utcnow()
                            execution.progress = 100

                            # Add log entry
                            log_entry = ExecutionLog(
                                text=(
                                    "Cancelled by celery task "
                                    "'monitor_failed_docker_services' "
                                    "- Docker service detected in restart "
                                    "loop or failed state."
                                ),
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
                                    f"[TASK]: Removing failed Docker service "
                                    f"{docker_service_name}"
                                )
                                service.remove()
                            except Exception as cleanup_error:
                                logger.warning(
                                    f"[TASK]: Failed to remove Docker service "
                                    f"{docker_service_name}: {cleanup_error}"
                                )
                        else:
                            # Get task summary for logging
                            tasks = service.tasks()
                            active_count = 0
                            failed_count = 0
                            for task in tasks:
                                task_state = task.get("Status", {}).get("State", "")
                                if task_state.lower() in [
                                    "running",
                                    "starting",
                                    "pending",
                                ]:
                                    active_count += 1
                                elif task_state.lower() in [
                                    "failed",
                                    "rejected",
                                    "shutdown",
                                ]:
                                    failed_count += 1

                            logger.debug(
                                f"[TASK]: Docker service {docker_service_name} "
                                f"is still healthy (active tasks: {active_count}, "
                                f"failed tasks: {failed_count})"
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
