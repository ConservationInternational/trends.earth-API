"""ASYNC EXECUTION CANCELLATION TASKS"""

import logging

from celery import Task
import rollbar

from gefapi import db
from gefapi.models import Execution, ExecutionLog, Script
from gefapi.services import terminate_batch_jobs
from gefapi.services.execution_service import (
    ExecutionService,
    update_execution_status_with_logging,
)

logger = logging.getLogger(__name__)


class ExecutionCancellationTask(Task):
    """Base task for execution cancellation."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error("Execution cancellation task failed: %s", exc)
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


@celery.task(
    base=ExecutionCancellationTask,
    bind=True,
    soft_time_limit=120,
    time_limit=180,
)
def cancel_execution_workflow(self, execution_id):
    """Perform execution cancellation in the background.

    The request path should only enqueue this task and return quickly.
    """
    logger.info("[TASK]: Starting async cancellation for execution %s", execution_id)

    from gefapi import app

    with app.app_context():
        execution = Execution.query.filter(Execution.id == execution_id).first()
        if not execution:
            logger.warning("[TASK]: Execution %s not found", execution_id)
            return {"execution_id": execution_id, "status": "NOT_FOUND"}

        if execution.status in ["FINISHED", "FAILED", "CANCELLED"]:
            logger.info(
                "[TASK]: Execution %s already terminal in status %s",
                execution.id,
                execution.status,
            )
            return {
                "execution_id": str(execution.id),
                "status": execution.status,
                "already_terminal": True,
            }

        if execution.status != "CANCELLING":
            logger.info(
                "[TASK]: Execution %s status is %s; skipping cancellation worker",
                execution.id,
                execution.status,
            )
            return {
                "execution_id": str(execution.id),
                "status": execution.status,
                "skipped": True,
            }

        from gefapi.services.gee_service import GEEService

        script = Script.query.get(execution.script_id) if execution.script_id else None
        is_batch = ExecutionService._is_batch_environment(script)

        cancellation_results = {
            "execution_id": str(execution.id),
            "previous_status": "CANCELLING",
            "docker_service_stopped": False,
            "docker_container_stopped": False,
            "batch_jobs_terminated": [],
            "gee_tasks_cancelled": [],
            "errors": [],
        }

        if is_batch:
            try:
                logger.info(
                    "[TASK]: Terminating Batch jobs for execution %s",
                    execution.id,
                )
                batch_results = terminate_batch_jobs(
                    str(execution.id),
                    reason="Cancelled by async cancellation workflow",
                )
                cancellation_results["batch_jobs_terminated"] = batch_results.get(
                    "jobs_terminated", []
                )
                cancellation_results["errors"].extend(batch_results.get("errors", []))
            except Exception as batch_error:
                msg = f"Batch cancellation failed: {batch_error}"
                logger.error("[TASK]: %s", msg)
                cancellation_results["errors"].append(msg)
                rollbar.report_exc_info()
        else:
            try:
                logger.info(
                    "[TASK]: Running docker cancellation for execution %s",
                    execution.id,
                )
                # Avoid waiting on a nested Celery task within a task.
                from gefapi.services.docker_service import cancel_execution_task

                docker_results = cancel_execution_task(str(execution.id))
                cancellation_results["docker_service_stopped"] = docker_results.get(
                    "docker_service_stopped", False
                )
                cancellation_results["docker_container_stopped"] = docker_results.get(
                    "docker_container_stopped", False
                )
                cancellation_results["errors"].extend(docker_results.get("errors", []))
            except Exception as docker_error:
                msg = f"Docker cancellation task failed: {docker_error}"
                logger.error("[TASK]: %s", msg)
                cancellation_results["errors"].append(msg)
                rollbar.report_exc_info()

        if not is_batch:
            try:
                logs = (
                    ExecutionLog.query.filter(ExecutionLog.execution_id == execution.id)
                    .order_by(ExecutionLog.register_date)
                    .all()
                )
                log_texts = [log.text for log in logs if log.text]
                if log_texts:
                    gee_results = GEEService.cancel_gee_tasks_from_execution(log_texts)
                    cancellation_results["gee_tasks_cancelled"] = gee_results
                    for gee_result in gee_results:
                        if not gee_result.get("success", False):
                            err = gee_result.get("error", "Unknown error")
                            if "permission" not in err.lower():
                                cancellation_results["errors"].append(
                                    "GEE task "
                                    f"{gee_result.get('task_id', 'unknown')}: {err}"
                                )
            except Exception as gee_error:
                msg = f"GEE task cancellation error: {gee_error}"
                logger.error("[TASK]: %s", msg)
                cancellation_results["errors"].append(msg)
                rollbar.report_exc_info()

        cancellation_summary = []
        if cancellation_results["batch_jobs_terminated"]:
            terminated = [
                result
                for result in cancellation_results["batch_jobs_terminated"]
                if result.get("success")
            ]
            total = len(cancellation_results["batch_jobs_terminated"])
            cancellation_summary.append(
                f"{len(terminated)}/{total} Batch jobs terminated"
            )
        if cancellation_results["docker_service_stopped"]:
            cancellation_summary.append("Docker service stopped")
        if cancellation_results["docker_container_stopped"]:
            cancellation_summary.append("Docker container stopped")
        if cancellation_results["gee_tasks_cancelled"]:
            successful_cancellations = len(
                [
                    result
                    for result in cancellation_results["gee_tasks_cancelled"]
                    if result.get("success")
                ]
            )
            cancellation_summary.append(
                f"{successful_cancellations}/"
                f"{len(cancellation_results['gee_tasks_cancelled'])} "
                "GEE tasks cancelled"
            )

        summary_text = "; ".join(cancellation_summary)
        if not summary_text:
            summary_text = "No active resources found to cancel."
        log_text = f"Execution cancelled by user. {summary_text}"
        if cancellation_results["errors"]:
            error_text = "; ".join(cancellation_results["errors"][:3])
            log_text += f" Errors: {error_text}"

        cancellation_log = ExecutionLog(
            text=log_text,
            level="INFO",
            execution_id=execution.id,
        )

        try:
            update_execution_status_with_logging(
                execution,
                "CANCELLED",
                additional_objects=[cancellation_log],
            )
            logger.info(
                "[TASK]: Async cancellation completed for execution %s",
                execution.id,
            )
        except Exception:
            rollbar.report_exc_info()
            db.session.rollback()
            raise

        return {
            "execution": execution.serialize(),
            "cancellation_details": cancellation_results,
        }
