"""openEO execution monitoring task.

Periodically polls openEO backends for the status of executions
dispatched via :func:`gefapi.services.openeo_service.openeo_run`.
When a job reaches a terminal state, this task updates the
``Execution`` record in the database:

* **finished** – sets ``execution.status = "FINISHED"`` and stores
  the results metadata in ``execution.results``.
* **error** – sets ``execution.status = "FAILED"`` with the backend
  error detail in ``execution.results["openeo_error"]``.
* **canceled** – sets ``execution.status = "CANCELLED"``.

This centralises lifecycle management in the API so that individual
script containers do **not** need to authenticate with or call back to
the API.  The openEO process graph is responsible for writing GeoTIFF
outputs to S3 via a ``save_result`` step.

Status transitions
~~~~~~~~~~~~~~~~~~
openEO job statuses (per the openEO API spec):

* ``created`` → ``queued`` → ``running`` → ``finished``
                                          ↘ ``error``
* ``canceled`` (terminal, set by user)
"""

import datetime
import logging

from celery import Task
import rollbar
from sqlalchemy import and_

from gefapi import db
from gefapi.models import Execution, ExecutionLog, Script, User

logger = logging.getLogger(__name__)

# Import openeo at module level for test mocking, but allow ImportError
try:
    import openeo  # type: ignore
except ImportError:
    openeo = None  # type: ignore

# How far back to look for active openEO executions.
_LOOKBACK_DAYS = 3

# openEO terminal statuses per the openEO API spec
_OPENEO_TERMINAL = frozenset({"finished", "error", "canceled"})

# Mapping from openEO terminal status → Execution.status
_STATUS_MAP = {
    "finished": "FINISHED",
    "error": "FAILED",
    "canceled": "CANCELLED",
}


class OpenEOMonitoringTask(Task):
    """Base task class for openEO monitoring."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error("openEO monitoring task failed: %s", exc)
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency.
from gefapi import celery  # noqa: E402


@celery.task(base=OpenEOMonitoringTask, bind=True)
def monitor_openeo_jobs(self):
    """Poll openEO backends for active executions and update the DB.

    This task is designed to run every 60 seconds via Celery Beat.  It:

    1. Queries the database for ``READY`` or ``RUNNING`` executions that
       belong to scripts with ``compute_type = "openeo"``.
    2. Groups executions by their backend URL for efficient polling.
    3. Connects to each backend and calls ``describe_job``.
    4. For each execution, updates the record based on the job status.
    """
    logger.info("[OPENEO-MONITOR]: Starting openEO execution monitoring")

    from gefapi import app

    with app.app_context():
        try:
            cutoff = datetime.datetime.utcnow() - datetime.timedelta(
                days=_LOOKBACK_DAYS
            )

            active = (
                db.session.query(Execution)
                .join(Script, Execution.script_id == Script.id)
                .filter(
                    and_(
                        Execution.status.in_(["READY", "RUNNING"]),
                        Script.compute_type == "openeo",
                        Execution.start_date >= cutoff,
                    ),
                )
                .order_by(Execution.start_date.desc())
                .limit(200)
                .all()
            )

            logger.info(
                "[OPENEO-MONITOR]: Found %d active openEO executions", len(active)
            )
            if not active:
                return {"checked": 0, "finished": 0, "failed": 0, "cancelled": 0}

            finished_count = 0
            failed_count = 0
            cancelled_count = 0

            for execution in active:
                try:
                    result = _poll_execution(execution)
                    if result == "FINISHED":
                        finished_count += 1
                    elif result == "FAILED":
                        failed_count += 1
                    elif result == "CANCELLED":
                        cancelled_count += 1
                except Exception as exc:
                    logger.error(
                        "[OPENEO-MONITOR]: Error polling execution %s: %s",
                        execution.id,
                        exc,
                    )
                    rollbar.report_exc_info()

            db.session.commit()

            logger.info(
                "[OPENEO-MONITOR]: Done. checked=%d finished=%d failed=%d cancelled=%d",
                len(active),
                finished_count,
                failed_count,
                cancelled_count,
            )
            return {
                "checked": len(active),
                "finished": finished_count,
                "failed": failed_count,
                "cancelled": cancelled_count,
            }

        except Exception as exc:
            logger.error("[OPENEO-MONITOR]: Unexpected error: %s", exc)
            rollbar.report_exc_info()
            return {"error": str(exc)}


def _poll_execution(execution):
    """Poll one execution and update its status.

    Returns the new Execution.status string if the execution reached a
    terminal state, or ``None`` if it is still running.
    """
    results = execution.results or {}
    job_id = results.get("openeo_job_id")
    backend_url = results.get("openeo_backend_url")

    if not job_id:
        logger.warning(
            "[OPENEO-MONITOR]: Execution %s (status=%s) has no openeo_job_id "
            "in results — skipping",
            execution.id,
            execution.status,
        )
        return None

    if not backend_url:
        logger.warning(
            "[OPENEO-MONITOR]: Execution %s has no openeo_backend_url — skipping",
            execution.id,
        )
        return None

    try:
        if openeo is None:
            logger.error(
                "[OPENEO-MONITOR]: openeo package not installed "
                "— cannot poll execution %s",
                execution.id,
            )
            return None

        connection = openeo.connect(backend_url)

        # Authenticate using the owning user's stored credentials.
        # We look up the user directly rather than reading from execution.results
        # to avoid storing decrypted credentials outside the User model.
        user = User.query.get(execution.user_id) if execution.user_id else None
        if user is not None and user.has_openeo_credentials():
            creds = user.get_openeo_credentials() or {}
            cred_type = creds.get("type", "oidc_refresh_token")
            if cred_type == "basic":
                connection.authenticate_basic(
                    username=creds["username"], password=creds["password"]
                )
            elif cred_type in ("oidc_refresh_token", "oidc") and creds.get(
                "refresh_token"
            ):
                connection.authenticate_oidc_refresh_token(
                    client_id=creds["client_id"],
                    client_secret=creds.get("client_secret"),
                    refresh_token=creds["refresh_token"],
                    provider_id=creds.get("provider_id"),
                )

        job = connection.job(job_id)
        status_info = job.status()
        openeo_status = (
            status_info
            if isinstance(status_info, str)
            else status_info.get("status", "")
        )
    except Exception as exc:
        logger.error(
            "[OPENEO-MONITOR]: Failed to poll job %s for execution %s: %s",
            job_id,
            execution.id,
            exc,
        )
        return None

    logger.debug(
        "[OPENEO-MONITOR]: Execution %s: openEO status = %s",
        execution.id,
        openeo_status,
    )

    # Update status to RUNNING if still running
    if openeo_status == "running" and execution.status != "RUNNING":
        execution.status = "RUNNING"
        execution.results = {**results, "openeo_status": openeo_status}
        db.session.flush()
        return None

    if openeo_status not in _OPENEO_TERMINAL:
        # Still queued/running – update results with latest status
        execution.results = {**results, "openeo_status": openeo_status}
        db.session.flush()
        return None

    # Terminal state reached
    new_status = _STATUS_MAP[openeo_status]
    logger.info(
        "[OPENEO-MONITOR]: Execution %s reached terminal state %s → %s",
        execution.id,
        openeo_status,
        new_status,
    )

    updated_results = {
        **results,
        "openeo_status": openeo_status,
    }

    if openeo_status == "finished":
        # Try to retrieve result metadata from the job
        try:
            job_results = job.get_results()
            assets = (
                job_results.get_assets() if hasattr(job_results, "get_assets") else {}
            )
            updated_results["openeo_results"] = {
                "assets": {
                    name: {"href": asset.href}
                    for name, asset in (
                        assets.items() if hasattr(assets, "items") else {}.items()
                    )
                }
            }
        except Exception as exc:
            logger.warning(
                "[OPENEO-MONITOR]: Could not fetch results for job %s: %s",
                job_id,
                exc,
            )
    elif openeo_status == "error":
        try:
            logs = job.logs()
            error_msgs = [
                entry.get("message", "")
                for entry in (logs or [])
                if entry.get("level") in ("error", "ERROR")
            ]
            updated_results["openeo_error"] = (
                "; ".join(error_msgs) or "openEO job failed"
            )
        except Exception as exc:
            logger.warning(
                "[OPENEO-MONITOR]: Could not fetch error logs for job %s: %s",
                job_id,
                exc,
            )
            updated_results["openeo_error"] = "openEO job failed (logs unavailable)"

    execution.status = new_status
    execution.results = updated_results
    execution.end_date = datetime.datetime.utcnow()

    # Write a log entry
    log_entry = ExecutionLog(
        text=f"openEO job {job_id} reached status: {openeo_status}",
        level="INFO" if new_status == "FINISHED" else "ERROR",
        execution_id=execution.id,
    )
    db.session.add(log_entry)
    db.session.flush()

    return new_status
