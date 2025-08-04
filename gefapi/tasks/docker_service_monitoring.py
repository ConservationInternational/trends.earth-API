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
        # Get tasks for this service from the last 5 minutes (reduced window for faster detection)
        recent_cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=5)
        # Also check for very recent failures (last 2 minutes for aggressive detection)
        very_recent_cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=2)

        tasks = service.tasks()
        active_tasks = []
        failed_tasks = []
        very_recent_failed_tasks = []

        for task in tasks:
            task_status = task.get("Status", {})
            task_state = task_status.get("State", "")
            desired_state = task.get("DesiredState", "")

            # Parse timestamp
            timestamp_str = task_status.get("Timestamp", "")
            task_time = None
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
                    # Track very recent failures for aggressive restart loop detection
                    if task_time and task_time >= very_recent_cutoff:
                        very_recent_failed_tasks.append(task)

        # Enhanced failure detection logic:

        # 1. Classic failure: No active tasks AND recent failed tasks
        if not active_tasks and failed_tasks and len(failed_tasks) >= 1:
            logger.info(
                f"Service {service.name} considered failed (no active tasks): "
                f"{len(active_tasks)} active tasks, {len(failed_tasks)} failed tasks"
            )
            return True

        # 2. Restart loop detection: Multiple failed tasks regardless of active tasks
        if len(failed_tasks) >= 2:
            logger.warning(
                f"Service {service.name} showing restart loop pattern: "
                f"{len(active_tasks)} active tasks, {len(failed_tasks)} failed tasks"
            )
            return True

        # 3. Aggressive recent failure detection: Multiple failures in last 5 minutes
        if len(very_recent_failed_tasks) >= 2:
            logger.warning(
                f"Service {service.name} showing rapid failure pattern: "
                f"{len(very_recent_failed_tasks)} failures in last 5 minutes, "
                f"{len(active_tasks)} active tasks"
            )
            return True

        # 4. Single active task with recent failures indicates potential restart loop
        if len(active_tasks) == 1 and len(failed_tasks) >= 1:
            logger.warning(
                f"Service {service.name} showing potential restart loop: "
                f"1 active task with {len(failed_tasks)} recent failures"
            )
            return True

        # 5. AGGRESSIVE: Even single failure with active task in very recent time (last 2 minutes)
        ultra_recent_cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=2)
        ultra_recent_failures = []
        for task in very_recent_failed_tasks:
            task_status = task.get("Status", {})
            timestamp_str = task_status.get("Timestamp", "")
            try:
                if timestamp_str:
                    timestamp_str = timestamp_str.replace("Z", "+00:00")
                    task_time = datetime.datetime.fromisoformat(
                        timestamp_str.replace("Z", "+00:00")
                    )
                    if task_time >= ultra_recent_cutoff:
                        ultra_recent_failures.append(task)
            except (ValueError, TypeError):
                # If we can't parse timestamp, include it anyway for safety
                ultra_recent_failures.append(task)

        if len(ultra_recent_failures) >= 1 and len(active_tasks) >= 1:
            logger.warning(
                f"Service {service.name} showing immediate restart pattern: "
                f"{len(ultra_recent_failures)} failures in last 2 minutes with "
                f"{len(active_tasks)} active tasks - likely restart loop"
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
    Runs every 1 minute to quickly detect restart loops and failed executions.

    Checks executions in PENDING, READY, RUNNING, or FAILED states because:
    - READY: Executions that just started (Docker service created)
    - RUNNING: Executions that are currently running
    - FAILED: Executions that failed but may be restarting due to restart policy
    - PENDING: Executions that are queued to start
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
            active_executions = (
                db.session.query(Execution)
                .filter(Execution.status.in_(["PENDING", "READY", "RUNNING", "FAILED"]))
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
                            text="Cancelled by celery task 'monitor_failed_docker_services'.",
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
                                    "Cancelled by celery task 'monitor_failed_docker_services' - "
                                    "Docker service detected in restart loop or failed state."
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
                                if task_state in ["running", "starting", "pending"]:
                                    active_count += 1
                                elif task_state in ["failed", "rejected", "shutdown"]:
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
