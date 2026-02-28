"""AWS Batch dispatch service for remote script executions.

This service provides a general-purpose Celery task and helpers to submit
script executions to **AWS Batch** rather than to a local Docker daemon.
It is the Batch counterpart of ``docker_service.docker_run``.

Design
------
The service is intentionally *script-agnostic*.  Any ``Script`` whose
``compute_type`` is ``"batch"`` will be dispatched here.  The Batch job
definition, queue, command, and resource requirements are read from:

1. The ``Script`` model columns (``batch_job_definition``,
   ``batch_job_queue``).
2. Execution parameters (``params["batch"]`` override block).
3. Environment-variable defaults as a last resort.

This makes it trivial to on-board new analysis scripts that need Batch
compute – just set ``compute_type="batch"`` on the Script and,
optionally, configure the job definition / queue.

Multi-step pipelines
~~~~~~~~~~~~~~~~~~~~
If ``params`` contains a ``"pipeline"`` key, its value is expected to be
a list of step descriptors::

    "pipeline": [
        {"name": "extract", "command": ["extract", "--config", "..."]},
        {"name": "match",   "command": ["match", "--config", "..."],
         "array_size": 10},
        {"name": "summarize", "command": ["summarize", "--config", "..."]}
    ]

Steps are submitted sequentially; each step depends on the previous one.
When ``array_size`` is present, the step is submitted as an AWS Batch
array job.  Single-step executions omit the ``pipeline`` key altogether
and the container runs the default command from its job definition.
"""

import gzip
import json
import logging
import os
from pathlib import Path
import tempfile

import boto3
import rollbar

from gefapi import celery as celery_app  # Rename to avoid mypy confusion
from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import Execution, Script

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default configuration (override per-script or per-execution)
# ---------------------------------------------------------------------------

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DEFAULT_BATCH_JOB_QUEUE = os.getenv("BATCH_JOB_QUEUE", "trends-earth-queue")
DEFAULT_BATCH_JOB_DEFINITION = os.getenv("BATCH_JOB_DEFINITION", "trends-earth-default")
PARAMS_S3_BUCKET = os.getenv("PARAMS_S3_BUCKET", "") or SETTINGS.get(
    "PARAMS_S3_BUCKET", ""
)
PARAMS_S3_PREFIX = os.getenv("PARAMS_S3_PREFIX", "execution_params") or SETTINGS.get(
    "PARAMS_S3_PREFIX", "execution_params"
)

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("BATCH_TIMEOUT_SECONDS", "14400"))


# ---------------------------------------------------------------------------
# AWS client factories
# ---------------------------------------------------------------------------


def _get_s3_client():
    return boto3.client("s3", region_name=AWS_REGION)


def _get_batch_client():
    return boto3.client("batch", region_name=AWS_REGION)


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------


def push_params_to_s3(params_dict, execution_id):
    """Serialise *params_dict* as compressed JSON and upload to S3.

    Returns the ``s3://`` URI of the uploaded object.
    """
    key = f"{PARAMS_S3_PREFIX}/{execution_id}.json.gz"
    with tempfile.TemporaryDirectory() as d:
        gz_path = Path(d) / f"{execution_id}.json.gz"
        data = json.dumps(params_dict).encode()
        with gzip.open(gz_path, "wb") as fp:
            fp.write(data)
        _get_s3_client().upload_file(str(gz_path), PARAMS_S3_BUCKET, key)
    return f"s3://{PARAMS_S3_BUCKET}/{key}"


# ---------------------------------------------------------------------------
# Batch job submission – generic helpers
# ---------------------------------------------------------------------------


def _build_container_env(environment, execution_id, config_s3_uri):
    """Build the ``environment`` list for Batch container overrides.

    Includes standard variables needed by *any* trends.earth execution
    container (API URL, credentials, S3 locations, Rollbar token, etc.).
    """
    env_vars = {
        "EXECUTION_ID": str(execution_id),
        "CONFIG_S3_URI": config_s3_uri,
        "API_URL": environment.get("API_URL", os.getenv("API_URL", "")),
        "API_USER": environment.get("API_USER", ""),
        "API_PASSWORD": environment.get("API_PASSWORD", ""),
        "PARAMS_S3_BUCKET": PARAMS_S3_BUCKET,
        "PARAMS_S3_PREFIX": PARAMS_S3_PREFIX,
        "AWS_DEFAULT_REGION": AWS_REGION,
        "ROLLBAR_SCRIPT_TOKEN": environment.get(
            "ROLLBAR_SCRIPT_TOKEN",
            os.getenv("ROLLBAR_SCRIPT_TOKEN", ""),
        ),
        "ROLLBAR_ENVIRONMENT": environment.get(
            "ROLLBAR_ENVIRONMENT",
            os.getenv("ENVIRONMENT", "development"),
        ),
    }
    # Merge any extra env vars the caller provided
    extra = environment.get("_batch_extra_env", {})
    env_vars.update(extra)

    return [{"name": k, "value": str(v)} for k, v in env_vars.items() if v]


def submit_single_job(
    execution_id,
    config_s3_uri,
    environment,
    *,
    job_queue=None,
    job_definition=None,
    command=None,
    timeout_seconds=None,
):
    """Submit a single AWS Batch job for an execution.

    This is the simplest dispatch mode: one execution → one Batch job.
    The container runs its default command unless *command* is provided.

    Returns
    -------
    dict
        ``{"job_id": "…"}``
    """
    client = _get_batch_client()
    container_env = _build_container_env(environment, execution_id, config_s3_uri)

    overrides = {"environment": container_env}
    if command:
        overrides["command"] = command

    resp = client.submit_job(
        jobName=f"te-{execution_id[:8]}",
        jobQueue=job_queue or DEFAULT_BATCH_JOB_QUEUE,
        jobDefinition=job_definition or DEFAULT_BATCH_JOB_DEFINITION,
        containerOverrides=overrides,
        timeout={"attemptDurationSeconds": timeout_seconds or DEFAULT_TIMEOUT_SECONDS},
    )
    return {"job_id": resp["jobId"]}


def submit_pipeline(
    execution_id,
    config_s3_uri,
    environment,
    steps,
    *,
    job_queue=None,
    job_definition=None,
    timeout_seconds=None,
):
    """Submit a multi-step pipeline to AWS Batch with inter-step dependencies.

    Parameters
    ----------
    steps : list[dict]
        Each dict may contain:

        * ``name`` (str, required) – human-readable label
        * ``command`` (list[str], optional) – container command override
        * ``array_size`` (int, optional) – submit as an array job
        * ``job_definition`` (str, optional) – per-step override
        * ``job_queue`` (str, optional) – per-step override
        * ``timeout_seconds`` (int, optional) – per-step override

    Returns
    -------
    dict
        ``{"step_name": "batch_job_id", …}``
    """
    client = _get_batch_client()
    container_env = _build_container_env(environment, execution_id, config_s3_uri)
    default_timeout = timeout_seconds or DEFAULT_TIMEOUT_SECONDS
    default_queue = job_queue or DEFAULT_BATCH_JOB_QUEUE
    default_def = job_definition or DEFAULT_BATCH_JOB_DEFINITION

    job_ids = {}
    prev_job_id = None

    for step in steps:
        name = step["name"]
        step_def = step.get("job_definition", default_def)
        step_queue = step.get("job_queue", default_queue)
        step_timeout = step.get("timeout_seconds", default_timeout)

        overrides = {"environment": container_env}
        if step.get("command"):
            overrides["command"] = step["command"]

        submit_kwargs = {
            "jobName": f"te-{name}-{execution_id[:8]}",
            "jobQueue": step_queue,
            "jobDefinition": step_def,
            "containerOverrides": overrides,
            "timeout": {"attemptDurationSeconds": step_timeout},
        }

        if step.get("array_size"):
            submit_kwargs["arrayProperties"] = {"size": max(step["array_size"], 1)}

        if prev_job_id:
            submit_kwargs["dependsOn"] = [{"jobId": prev_job_id, "type": "SEQUENTIAL"}]

        resp = client.submit_job(**submit_kwargs)
        job_id = resp["jobId"]
        job_ids[name] = job_id
        prev_job_id = job_id

    return job_ids


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------


def get_batch_job_status(job_id):
    """Query AWS Batch for the current status of *job_id*.

    Returns a dict with at least ``job_id`` and ``status`` keys.
    """
    client = _get_batch_client()
    resp = client.describe_jobs(jobs=[job_id])
    if not resp["jobs"]:
        return {"job_id": job_id, "status": "NOT_FOUND"}
    job = resp["jobs"][0]
    result = {
        "job_id": job["jobId"],
        "status": job["status"],
        "created_at": job.get("createdAt"),
        "started_at": job.get("startedAt"),
        "stopped_at": job.get("stoppedAt"),
    }
    if job.get("arrayProperties"):
        result["array_size"] = job["arrayProperties"].get("size")
        result["array_status"] = job["arrayProperties"].get("statusSummary", {})
    if job["status"] == "FAILED":
        result["reason"] = job.get("statusReason", "Unknown")
    return result


# ---------------------------------------------------------------------------
# Celery task – counterpart of ``docker_run`` for Batch-based executions
# ---------------------------------------------------------------------------


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60)
def batch_run(self, execution_id, image, environment, params):
    """Celery task: push params to S3 and submit to AWS Batch.

    Works for both single-job and multi-step pipeline executions.

    Dispatch logic
    ~~~~~~~~~~~~~~
    * If ``params`` contains a ``"pipeline"`` key, the value is treated
      as a list of step descriptors and submitted via
      :func:`submit_pipeline`.
    * Otherwise a single Batch job is submitted via
      :func:`submit_single_job`.

    The job definition and queue are resolved (in priority order) from:

    1. ``params["batch"]`` override block
    2. The Script model's ``batch_job_definition`` / ``batch_job_queue``
    3. Environment-variable defaults
    """
    logger.info("[BATCH] Starting batch_run for execution %s", execution_id)

    execution = Execution.query.get(execution_id)
    if not execution:
        logger.error("[BATCH] Execution %s not found", execution_id)
        return

    try:
        execution.status = "READY"
        db.session.add(execution)
        db.session.commit()

        # ---- resolve Batch settings ----
        batch_overrides = params.get("batch", {})
        script = Script.query.get(execution.script_id) if execution.script_id else None

        job_queue = (
            batch_overrides.get("job_queue")
            or (getattr(script, "batch_job_queue", None) if script else None)
            or DEFAULT_BATCH_JOB_QUEUE
        )
        job_definition = (
            batch_overrides.get("job_definition")
            or (getattr(script, "batch_job_definition", None) if script else None)
            or DEFAULT_BATCH_JOB_DEFINITION
        )
        timeout = batch_overrides.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        command = batch_overrides.get("command")

        # ---- push params to S3 ----
        config_s3_uri = push_params_to_s3(params, str(execution_id))
        logger.info("[BATCH] Params uploaded to %s", config_s3_uri)

        # ---- submit ----
        pipeline_steps = params.get("pipeline")
        if pipeline_steps:
            job_ids = submit_pipeline(
                str(execution_id),
                config_s3_uri,
                environment,
                pipeline_steps,
                job_queue=job_queue,
                job_definition=job_definition,
                timeout_seconds=timeout,
            )
        else:
            job_ids = submit_single_job(
                str(execution_id),
                config_s3_uri,
                environment,
                job_queue=job_queue,
                job_definition=job_definition,
                command=command,
                timeout_seconds=timeout,
            )

        logger.info("[BATCH] Submitted jobs: %s", job_ids)

        # Store Batch job IDs for tracking; final results will overwrite.
        execution.results = {"batch_jobs": job_ids, "status": "SUBMITTED"}
        execution.status = "RUNNING"
        db.session.add(execution)
        db.session.commit()

    except Exception as exc:
        logger.error("[BATCH] Failed to submit for %s: %s", execution_id, exc)
        try:
            execution.status = "FAILED"
            db.session.add(execution)
            db.session.commit()
        except Exception:
            logger.error("[BATCH] Could not mark execution as FAILED")
        rollbar.report_exc_info()
        raise self.retry(exc=exc) from None
