"""DOCKER SERVICE MONITORING TASKS"""

import datetime
import logging

from celery import Task
import rollbar

from gefapi import db
from gefapi.models import Execution, ExecutionLog
from gefapi.services.docker_service import get_docker_client

logger = logging.getLogger(__name__)

RESTART_LOOP_THRESHOLD = 3


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

    Distinguishes between actual failures (non-zero exit, rejected) and
    orchestrator-driven shutdowns (node drain, node failure, rolling update).
    Tasks in the "shutdown" state are normal during Swarm node failures and
    should NOT be counted as application failures — the Swarm orchestrator
    will reschedule them on a healthy node.

    Restart-loop detection: if a service has accumulated >=3 real failures
    (failed/rejected) it is considered to be in a restart loop even when a
    current attempt is still active, because the restart policy will keep
    trying and failing.

    Args:
        service: Docker service object

    Returns:
        bool: True if service is considered failed, False otherwise
    """
    # Threshold: how many real (non-shutdown) failures indicate a restart loop.
    # With the execution restart policy set to max_attempts=5, declaring a loop
    # at 3 gives the monitoring task a chance to act before all retries expire.

    try:
        tasks = service.tasks()

        # Count task states - distinguish real failures from orchestrator events
        active_tasks = 0
        failed_tasks = 0
        shutdown_tasks = 0

        for task in tasks:
            task_status = task.get("Status", {})
            task_state = task_status.get("State", "").lower()
            desired_state = task.get("DesiredState", "").lower()

            # Count active tasks (only those that should be running)
            if desired_state == "running" and task_state in [
                "running",
                "starting",
                "pending",
            ]:
                active_tasks += 1

            # Count real application failures (non-zero exit, resource rejection)
            # NOTE: "shutdown" is excluded — it indicates the Swarm orchestrator
            # stopped the task (node drain, node failure, rolling update), NOT an
            # application error. Swarm will reschedule these on a healthy node.
            if task_state in ["failed", "rejected"]:
                failed_tasks += 1

            # Track shutdowns separately for diagnostics
            if task_state == "shutdown":
                shutdown_tasks += 1

        logger.debug(
            f"Service {service.name}: {active_tasks} active, "
            f"{failed_tasks} failed, {shutdown_tasks} shutdown tasks"
        )

        # Failure detection rules:

        # 1. Restart-loop detection: many real failures means the service keeps
        #    crashing regardless of whether a new attempt is currently active.
        if failed_tasks >= RESTART_LOOP_THRESHOLD:
            logger.warning(
                f"Service {service.name} in restart loop: "
                f"{failed_tasks} real failures (threshold {RESTART_LOOP_THRESHOLD}), "
                f"{active_tasks} active, {shutdown_tasks} shutdown"
            )
            return True

        # 2. No active tasks and real failures exist = service has failed
        if active_tasks == 0 and failed_tasks > 0:
            logger.warning(
                f"Service {service.name} failed: no active tasks, "
                f"{failed_tasks} failed, {shutdown_tasks} shutdown"
            )
            return True

        # 3. Active task with a small number of real failures (<threshold):
        #    Swarm may be recovering from a transient error. Let it continue.
        if active_tasks > 0 and failed_tasks > 0:
            logger.info(
                f"Service {service.name} has {active_tasks} active task(s) "
                f"alongside {failed_tasks} failure(s) (below threshold "
                f"{RESTART_LOOP_THRESHOLD}) — allowing Swarm to recover"
            )
            return False

        # 4. No active tasks, no real failures, only shutdowns = node went
        #    down. Swarm should reschedule; give it time before declaring failure.
        if active_tasks == 0 and shutdown_tasks > 0:
            logger.info(
                f"Service {service.name} has {shutdown_tasks} shutdown task(s) "
                f"but no failures — likely a node went down. "
                f"Swarm should reschedule automatically; not marking as failed."
            )
            return False

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

    To prevent endless monitoring loops, executions with no Docker service
    (already cleaned up) are skipped rather than re-marked as failed.

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
                    100
                )  # Limit to 100 most recent executions to prevent memory issues
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
                        # Service doesn't exist - check if execution is already failed
                        if execution.status == "FAILED":
                            # Execution is already failed and service is gone - skip it
                            # to prevent endless monitoring loops
                            logger.debug(
                                f"[TASK]: Execution {execution.id} is already FAILED "
                                f"and Docker service is gone - skipping"
                            )
                            continue

                        # Service doesn't exist and execution is not failed yet
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
                            # Get task summary for logging - use same logic
                            tasks = service.tasks()
                            active_count = 0
                            failed_count = 0
                            shutdown_count = 0
                            for task in tasks:
                                task_status = task.get("Status", {})
                                task_state = task_status.get("State", "").lower()
                                desired_state = task.get("DesiredState", "").lower()

                                # Count active tasks (only those that should be running)
                                if desired_state == "running" and task_state in [
                                    "running",
                                    "starting",
                                    "pending",
                                ]:
                                    active_count += 1

                                # Count real failures (not shutdowns)
                                if task_state in [
                                    "failed",
                                    "rejected",
                                ]:
                                    failed_count += 1

                                if task_state == "shutdown":
                                    shutdown_count += 1

                            logger.debug(
                                f"[TASK]: Docker service {docker_service_name} "
                                f"is still healthy (active: {active_count}, "
                                f"failed: {failed_count}, "
                                f"shutdown: {shutdown_count})"
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
