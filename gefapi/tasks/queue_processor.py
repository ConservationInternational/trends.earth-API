"""EXECUTION QUEUE PROCESSOR TASK

Processes queued executions (PENDING status with queued_at set) in FIFO order.
When a user's active execution count drops below the configured limit, their
oldest queued execution is dispatched.

Admin and superadmin users are exempt from queueing and should never have
queued executions, but this is handled defensively.
"""

import logging

from celery import Task
import rollbar

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import Execution, Script, User
from gefapi.utils.permissions import is_admin_or_higher

logger = logging.getLogger(__name__)

# Maximum users to process per task invocation to prevent long-running tasks
_MAX_USERS_PER_RUN = 100


class QueueProcessorTask(Task):
    """Base task for queue processing"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Queue processor task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


def _get_user_active_execution_count(user_id):
    """Count executions in active states for a user.

    Active states are those that consume resources:
    - PENDING (without queued_at): About to start
    - READY: Container is ready/starting
    - RUNNING: Currently executing

    Excludes PENDING executions with queued_at set (those are in the queue).
    """
    return Execution.query.filter(
        Execution.user_id == user_id,
        Execution.status.in_(["PENDING", "READY", "RUNNING"]),
        Execution.queued_at.is_(None),
    ).count()


def _dispatch_queued_execution(execution, user, script):
    """Dispatch a queued execution.

    Clears the queued_at timestamp and dispatches to the appropriate runner.
    """
    # Import here to avoid circular dependency
    from gefapi.services import batch_run, docker_run
    from gefapi.services.execution_service import ExecutionService

    # Clear the queued_at timestamp
    execution.queued_at = None
    db.session.commit()

    # Build environment and dispatch
    environment = ExecutionService._build_execution_environment(
        user, execution.id, script=script
    )

    orchestrator = SETTINGS.get("ORCHESTRATOR", "docker")
    is_batch = (getattr(script, "compute_type", None) or "").lower() == "batch"

    if orchestrator == "docker":
        if is_batch:
            batch_run.delay(execution.id, script.slug, environment, execution.params)
        else:
            docker_run.delay(execution.id, script.slug, environment, execution.params)
    else:
        raise ValueError(f"Unknown orchestrator: {orchestrator}")

    logger.info(
        f"[QUEUE]: Dispatched queued execution {execution.id} for user {user.id}"
    )


@celery.task(base=QueueProcessorTask, bind=True)
def process_queued_executions(self):
    """Process queued executions in FIFO order.

    For each user with queued executions, checks if they have capacity
    (active count < max_concurrent) and dispatches their oldest queued
    execution.

    This task runs periodically (configured via QUEUE_PROCESSOR_INTERVAL).
    """
    logger.info("[TASK]: Starting queued execution processing")

    # Import here to get the app instance
    from gefapi import app

    with app.app_context():
        try:
            queue_config = SETTINGS.get("EXECUTION_QUEUE", {})
            if not queue_config.get("ENABLED", True):
                logger.info("[TASK]: Execution queue is disabled")
                return {"processed": 0, "skipped": 0, "errors": 0}

            max_concurrent = queue_config.get("MAX_CONCURRENT_PER_USER", 3)

            # Find users with queued executions (PENDING with queued_at set)
            users_with_queued = (
                db.session.query(Execution.user_id)
                .filter(
                    Execution.status == "PENDING",
                    Execution.queued_at.isnot(None),
                )
                .distinct()
                .limit(_MAX_USERS_PER_RUN)
                .all()
            )

            if not users_with_queued:
                logger.info("[TASK]: No users with queued executions")
                return {"processed": 0, "skipped": 0, "errors": 0}

            logger.info(
                f"[TASK]: Found {len(users_with_queued)} users with queued executions"
            )

            processed = 0
            skipped = 0
            errors = 0

            for (user_id,) in users_with_queued:
                try:
                    user = User.query.get(user_id)
                    if not user:
                        logger.warning(f"[QUEUE]: User {user_id} not found, skipping")
                        skipped += 1
                        continue

                    # Safety check: admins shouldn't have queued executions
                    if is_admin_or_higher(user):
                        logger.warning(
                            f"[QUEUE]: Admin user {user_id} has queued executions. "
                            "Dispatching all immediately."
                        )
                        # Dispatch all queued executions for admin
                        admin_queued = Execution.query.filter(
                            Execution.user_id == user_id,
                            Execution.status == "PENDING",
                            Execution.queued_at.isnot(None),
                        ).all()
                        for exec_item in admin_queued:
                            script = Script.query.get(exec_item.script_id)
                            if script:
                                _dispatch_queued_execution(exec_item, user, script)
                                processed += 1
                        continue

                    # Check user's current active execution count
                    active_count = _get_user_active_execution_count(user_id)

                    # Per-user override takes precedence over global default
                    user_limit = max_concurrent
                    if user.max_concurrent_executions is not None:
                        user_limit = user.max_concurrent_executions

                    if active_count >= user_limit:
                        logger.debug(
                            f"[QUEUE]: User {user_id} still at limit "
                            f"({active_count}/{user_limit}), skipping"
                        )
                        skipped += 1
                        continue

                    # Calculate how many slots are available
                    available_slots = user_limit - active_count

                    # Get oldest queued executions for this user (FIFO order)
                    queued_executions = (
                        Execution.query.filter(
                            Execution.user_id == user_id,
                            Execution.status == "PENDING",
                            Execution.queued_at.isnot(None),
                        )
                        .order_by(Execution.queued_at.asc())
                        .limit(available_slots)
                        .all()
                    )

                    for execution in queued_executions:
                        try:
                            script = Script.query.get(execution.script_id)
                            if not script:
                                logger.error(
                                    f"[QUEUE]: Script {execution.script_id} not found "
                                    f"for execution {execution.id}"
                                )
                                errors += 1
                                continue

                            if script.status != "SUCCESS":
                                logger.warning(
                                    f"[QUEUE]: Script {execution.script_id} status is "
                                    f"{script.status}, not SUCCESS. Skipping execution "
                                    f"{execution.id}"
                                )
                                skipped += 1
                                continue

                            _dispatch_queued_execution(execution, user, script)
                            processed += 1

                        except Exception as exec_error:
                            logger.error(
                                f"[QUEUE]: Error dispatching execution "
                                f"{execution.id}: {exec_error}"
                            )
                            rollbar.report_exc_info()
                            errors += 1

                except Exception as user_error:
                    logger.error(
                        f"[QUEUE]: Error processing user {user_id}: {user_error}"
                    )
                    rollbar.report_exc_info()
                    errors += 1

            logger.info(
                f"[TASK]: Queue processing complete. "
                f"Processed: {processed}, Skipped: {skipped}, Errors: {errors}"
            )

            return {"processed": processed, "skipped": skipped, "errors": errors}

        except Exception as error:
            logger.error(f"[TASK]: Queue processing failed: {error}")
            rollbar.report_exc_info()
            raise
