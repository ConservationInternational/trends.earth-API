"""AWS Batch execution monitoring task.

Periodically polls AWS Batch for the status of executions dispatched via
``batch_run``.  When a Batch job reaches a terminal state, this task
updates the ``Execution`` record in the database:

* **SUCCEEDED** – downloads the results JSON from S3 and sets
  ``execution.status = "FINISHED"``.
* **FAILED** – sets ``execution.status = "FAILED"`` with the Batch
  failure reason.

This centralises lifecycle management in the API so that individual
script containers do **not** need to authenticate with or call back to
the API.  Containers only need S3 write access to deposit a results file.

Results convention
~~~~~~~~~~~~~~~~~~
The container writes its results payload to::

    s3://{PARAMS_S3_BUCKET}/{PARAMS_S3_PREFIX}/{execution_id}_results.json.gz

The monitor downloads that file and stores the parsed dict in
``execution.results``.
"""

import datetime
import gzip
import json
import logging
import os
from pathlib import Path
import tempfile

import boto3
from celery import Task
import rollbar
from sqlalchemy import and_

from gefapi import db
from gefapi.models import Execution, ExecutionLog, Script

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration – read from environment (same variables as batch_service)
# ---------------------------------------------------------------------------

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
PARAMS_S3_BUCKET = os.getenv("PARAMS_S3_BUCKET", "")
PARAMS_S3_PREFIX = os.getenv("PARAMS_S3_PREFIX", "execution_params")

# Batch terminal statuses
_BATCH_TERMINAL = frozenset({"SUCCEEDED", "FAILED"})

# How far back to look for active batch executions (matches the stale
# execution cleanup window).
_LOOKBACK_DAYS = 3


# ---------------------------------------------------------------------------
# Celery task boilerplate
# ---------------------------------------------------------------------------


class BatchMonitoringTask(Task):
    """Base task class for Batch monitoring."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error("Batch monitoring task failed: %s", exc)
        rollbar.report_exc_info()


# Import celery *after* other imports to avoid circular dependency – this
# follows the same pattern used by all task modules in the project.
from gefapi import celery  # noqa: E402

# ---------------------------------------------------------------------------
# AWS client helpers
# ---------------------------------------------------------------------------


def _batch_client():
    return boto3.client("batch", region_name=AWS_REGION)


def _s3_client():
    return boto3.client("s3", region_name=AWS_REGION)


# ---------------------------------------------------------------------------
# Core monitoring task
# ---------------------------------------------------------------------------


@celery.task(base=BatchMonitoringTask, bind=True)
def monitor_batch_executions(self):
    """Poll AWS Batch for active batch executions and update the DB.

    This task is designed to run every 2–3 minutes via Celery Beat.  It:

    1. Queries the database for ``RUNNING`` or ``READY`` executions that
       belong to scripts with ``compute_type = "batch"``.
    2. Collects the AWS Batch job IDs stored in ``execution.results``
       by the ``batch_run`` task.
    3. Calls ``DescribeJobs`` (batched, up to 100 per API call).
    4. For each execution, determines the aggregate status across all
       jobs (single-job or pipeline) and updates the execution record.
    """
    logger.info("[BATCH-MONITOR] Starting batch execution monitoring")

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
                        Script.compute_type == "batch",
                        Execution.start_date >= cutoff,
                    ),
                )
                .order_by(Execution.start_date.desc())
                .limit(200)
                .all()
            )

            logger.info("[BATCH-MONITOR] Found %d active batch executions", len(active))
            if not active:
                return {"checked": 0, "finished": 0, "failed": 0}

            # --- collect all Batch job IDs ---
            exec_jobs: dict = {}  # execution.id -> {name: job_id}
            all_job_ids: list[str] = []
            for execution in active:
                batch_jobs = (execution.results or {}).get("batch_jobs")
                if not batch_jobs:
                    logger.warning(
                        "[BATCH-MONITOR] Execution %s (status=%s) has "
                        "no batch_jobs in results — results=%s",
                        execution.id,
                        execution.status,
                        execution.results,
                    )
                    continue
                exec_jobs[execution.id] = batch_jobs
                all_job_ids.extend(batch_jobs.values())

            if not all_job_ids:
                logger.info("[BATCH-MONITOR] No Batch job IDs found to check")
                return {"checked": 0, "finished": 0, "failed": 0}

            # --- describe jobs in chunks of 100 ---
            client = _batch_client()
            job_details: dict = {}
            for i in range(0, len(all_job_ids), 100):
                chunk = all_job_ids[i : i + 100]
                try:
                    resp = client.describe_jobs(jobs=chunk)
                    for job in resp.get("jobs", []):
                        job_details[job["jobId"]] = job
                except Exception as exc:
                    logger.error(
                        "[BATCH-MONITOR] describe_jobs failed for chunk: %s", exc
                    )

            # --- update each execution ---
            finished_count = 0
            failed_count = 0

            for execution in active:
                batch_jobs = exec_jobs.get(execution.id)
                if not batch_jobs:
                    continue
                try:
                    result = _process_execution(execution, batch_jobs, job_details)
                    if result == "FINISHED":
                        finished_count += 1
                    elif result == "FAILED":
                        failed_count += 1
                except Exception as exc:
                    logger.error(
                        "[BATCH-MONITOR] Error processing execution %s: %s",
                        execution.id,
                        exc,
                    )
                    rollbar.report_exc_info()

            db.session.commit()

            logger.info(
                "[BATCH-MONITOR] Done. checked=%d finished=%d failed=%d",
                len(exec_jobs),
                finished_count,
                failed_count,
            )
            return {
                "checked": len(exec_jobs),
                "finished": finished_count,
                "failed": failed_count,
            }

        except Exception as exc:
            logger.error("[BATCH-MONITOR] Unexpected error: %s", exc)
            rollbar.report_exc_info()
            return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Per-execution processing
# ---------------------------------------------------------------------------


def _process_execution(execution, batch_jobs, job_details):
    """Evaluate Batch job statuses for *execution* and update the DB row.

    Returns ``"FINISHED"``, ``"FAILED"``, or ``None`` (still running).
    """
    statuses = {}
    for name, job_id in batch_jobs.items():
        job = job_details.get(job_id)
        if job:
            statuses[name] = {
                "status": job["status"],
                "reason": job.get("statusReason"),
                "started_at": str(job.get("startedAt", "")),
                "stopped_at": str(job.get("stoppedAt", "")),
            }
        else:
            statuses[name] = {"status": "NOT_FOUND"}

    all_job_statuses = [s["status"] for s in statuses.values()]
    logger.info(
        "[BATCH-MONITOR] Execution %s: job statuses = %s",
        execution.id,
        {name: info["status"] for name, info in statuses.items()},
    )

    # --- any failure → mark execution FAILED ---
    if any(s == "FAILED" for s in all_job_statuses):
        reasons = [
            f"{name}: {info.get('reason', 'unknown')}"
            for name, info in statuses.items()
            if info["status"] == "FAILED"
        ]
        execution.status = "FAILED"
        execution.end_date = datetime.datetime.utcnow()
        execution.results = {
            "batch_jobs": batch_jobs,
            "batch_statuses": statuses,
            "error": "; ".join(reasons),
        }

        log_entry = ExecutionLog(
            text=f"Batch job failed: {'; '.join(reasons)}",
            level="ERROR",
            execution_id=execution.id,
        )
        db.session.add(log_entry)
        db.session.add(execution)
        logger.info("[BATCH-MONITOR] Execution %s → FAILED", execution.id)
        rollbar.report_message(
            f"Batch execution {execution.id} failed: {'; '.join(reasons)}",
            level="error",
        )
        return "FAILED"

    # --- all succeeded → fetch results, mark FINISHED ---
    if all(s == "SUCCEEDED" for s in all_job_statuses):
        results = _fetch_results_from_s3(str(execution.id))
        if results is not None:
            execution.results = results
        else:
            # Container succeeded but no results file – store batch info
            execution.results = {
                "batch_jobs": batch_jobs,
                "batch_statuses": statuses,
                "note": "Batch job succeeded but no results file found in S3",
            }
            warn_log = ExecutionLog(
                text="Batch job succeeded but no results file was found in S3",
                level="WARN",
                execution_id=execution.id,
            )
            db.session.add(warn_log)
            logger.warning(
                "[BATCH-MONITOR] Execution %s succeeded but no results in S3",
                execution.id,
            )

        execution.status = "FINISHED"
        execution.end_date = datetime.datetime.utcnow()

        log_entry = ExecutionLog(
            text="Batch job completed successfully",
            level="INFO",
            execution_id=execution.id,
        )
        db.session.add(log_entry)
        db.session.add(execution)
        logger.info("[BATCH-MONITOR] Execution %s → FINISHED", execution.id)
        return "FINISHED"

    # --- still in progress → update visibility info ---
    current_results = dict(execution.results or {})
    current_results["batch_statuses"] = statuses
    execution.results = current_results
    db.session.add(execution)
    return None


# ---------------------------------------------------------------------------
# S3 results retrieval
# ---------------------------------------------------------------------------


def _fetch_results_from_s3(execution_id):
    """Download the results JSON deposited by the container.

    Convention: the container writes
    ``s3://{PARAMS_S3_BUCKET}/{PARAMS_S3_PREFIX}/{execution_id}_results.json.gz``

    Returns the parsed dict, or ``None`` if the file does not exist.
    """
    if not PARAMS_S3_BUCKET:
        logger.warning("[BATCH-MONITOR] PARAMS_S3_BUCKET is not configured")
        return None

    key = f"{PARAMS_S3_PREFIX}/{execution_id}_results.json.gz"
    logger.info(
        "[BATCH-MONITOR] Fetching results from s3://%s/%s", PARAMS_S3_BUCKET, key
    )
    try:
        s3 = _s3_client()
        with tempfile.TemporaryDirectory() as tmp:
            gz_path = Path(tmp) / "results.json.gz"
            s3.download_file(PARAMS_S3_BUCKET, key, str(gz_path))
            with gzip.open(gz_path, "r") as f:
                return json.loads(f.read().decode("utf-8"))
    except Exception as exc:
        error_code = ""
        if hasattr(exc, "response"):
            error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("NoSuchKey", "404"):
            logger.warning("[BATCH-MONITOR] Results file not found: %s", key)
        else:
            logger.error("[BATCH-MONITOR] Failed to fetch results: %s", exc)
        return None
