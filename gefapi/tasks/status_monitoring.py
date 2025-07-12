"""STATUS MONITORING TASKS"""

import contextlib
import logging

from celery import Task
import psutil
import rollbar
from sqlalchemy import func

from gefapi import db
from gefapi.models import Execution, Script, StatusLog, User

logger = logging.getLogger(__name__)


class StatusMonitoringTask(Task):
    """Base task for status monitoring"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Status monitoring task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


@celery.task(base=StatusMonitoringTask, bind=True)
def collect_system_status(self):
    """Collect system status and save to status_log table"""
    logger.info("[TASK]: Starting system status collection")

    # Import here to get the app instance
    from gefapi import app

    with app.app_context():
        try:
            # Count executions by status
            logger.info("[TASK]: Querying execution counts")
            execution_counts = (
                db.session.query(Execution.status, func.count(Execution.id))
                .group_by(Execution.status)
                .all()
            )

            execution_status_map = dict(execution_counts)
            executions_active = execution_status_map.get(
                "RUNNING", 0
            ) + execution_status_map.get("PENDING", 0)
            executions_ready = execution_status_map.get("READY", 0)
            executions_running = execution_status_map.get("RUNNING", 0)

            # Count executions finished and failed since the last status log
            logger.info(
                "[TASK]: Querying executions finished and failed since last status log"
            )
            last_status_log = (
                db.session.query(StatusLog).order_by(StatusLog.timestamp.desc()).first()
            )

            if last_status_log:
                # Count executions that finished after the last status log timestamp
                executions_finished = (
                    db.session.query(func.count(Execution.id))
                    .filter(
                        Execution.status == "FINISHED",
                        Execution.end_date > last_status_log.timestamp,
                    )
                    .scalar()
                    or 0
                )

                # Count executions that failed after the last status log timestamp
                executions_failed = (
                    db.session.query(func.count(Execution.id))
                    .filter(
                        Execution.status == "FAILED",
                        Execution.end_date > last_status_log.timestamp,
                    )
                    .scalar()
                    or 0
                )

                logger.info(
                    f"[TASK]: Found {executions_finished} executions finished and "
                    f"{executions_failed} executions failed since last status log at "
                    f"{last_status_log.timestamp}"
                )
            else:
                # If no previous status log exists, count all finished and failed
                # executions
                executions_finished = execution_status_map.get("FINISHED", 0)
                executions_failed = execution_status_map.get("FAILED", 0)
                logger.info(
                    "[TASK]: No previous status log found, counting all finished "
                    "and failed executions"
                )

            logger.info(
                f"[TASK]: Execution counts - Active: {executions_active}, "
                f"Running: {executions_running}, Finished: {executions_finished}, "
                f"Failed: {executions_failed}"
            )

            # Count total executions
            logger.info("[TASK]: Querying total execution count")
            executions_count = db.session.query(func.count(Execution.id)).scalar() or 0

            logger.info(f"[TASK]: Total executions count: {executions_count}")

            # Count users and scripts
            logger.info("[TASK]: Querying user and script counts")
            users_count = db.session.query(func.count(User.id)).scalar() or 0
            scripts_count = db.session.query(func.count(Script.id)).scalar() or 0

            logger.info(
                f"[TASK]: Counts - Users: {users_count}, Scripts: {scripts_count}"
            )

            # Get system metrics
            logger.info("[TASK]: Collecting system metrics")
            memory = psutil.virtual_memory()
            memory_available_percent = memory.available / memory.total * 100
            cpu_usage_percent = psutil.cpu_percent(interval=1)

            logger.info(
                f"[TASK]: System metrics - CPU: {cpu_usage_percent}%, "
                f"Memory Available: {memory_available_percent:.1f}%"
            )

            # Create status log entry
            logger.info("[TASK]: Creating status log entry")
            status_log = StatusLog(
                executions_active=executions_active,
                executions_ready=executions_ready,
                executions_running=executions_running,
                executions_finished=executions_finished,
                executions_failed=executions_failed,
                executions_count=executions_count,
                users_count=users_count,
                scripts_count=scripts_count,
                memory_available_percent=memory_available_percent,
                cpu_usage_percent=cpu_usage_percent,
            )

            logger.info("[DB]: Adding status log to database")
            db.session.add(status_log)
            db.session.commit()

            logger.info(
                f"[TASK]: Status log created successfully with ID {status_log.id} "
                f"at {status_log.timestamp}"
            )
            # Return serialized data for task result
            result = status_log.serialize()
            logger.info(f"[TASK]: Task completed successfully, returning: {result}")
            return result

        except Exception as error:
            logger.error(f"[TASK]: Error collecting system status: {str(error)}")
            logger.exception("Full traceback:")
            # Try to rollback the session
            with contextlib.suppress(Exception):
                db.session.rollback()

            # Report to rollbar if available
            with contextlib.suppress(Exception):
                rollbar.report_exc_info()

            # Re-raise the error so Celery can handle it
            raise error
