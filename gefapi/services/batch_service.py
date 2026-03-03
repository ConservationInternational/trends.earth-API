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
from gefapi.models import Execution, ExecutionLog, Script

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

# IAM roles for auto-registered job definitions
BATCH_JOB_ROLE_ARN = os.getenv("BATCH_JOB_ROLE_ARN", "")
BATCH_EXECUTION_ROLE_ARN = os.getenv("BATCH_EXECUTION_ROLE_ARN", "")
BATCH_DEFAULT_VCPUS = os.getenv("BATCH_DEFAULT_VCPUS", "4")
BATCH_DEFAULT_MEMORY_MIB = os.getenv("BATCH_DEFAULT_MEMORY_MIB", "30720")

# ECR registry prefix for automatic image resolution.
# When a script has no explicit ``batch_image``, the image URI is
# constructed as ``{ECR_REGISTRY}/{slug}:latest``.
# Set by the deploy workflow from the ECR login step output.
ECR_REGISTRY = os.getenv("ECR_REGISTRY", "")


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
# Job definition management
# ---------------------------------------------------------------------------


def _image_to_definition_name(image):
    """Derive a Batch job definition name from a container image URI.

    Examples
    --------
    >>> _image_to_definition_name("123.dkr.ecr.us-east-1.amazonaws.com/my-repo:v1")
    'my-repo'
    >>> _image_to_definition_name("my-repo:latest")
    'my-repo'
    """
    image_path = image.split("/")[-1]  # "my-repo:tag" or "my-repo"
    return image_path.split(":")[0]  # "my-repo"


def _ensure_job_definition(image, definition_name=None):
    """Ensure an AWS Batch job definition exists for *image*.

    If *definition_name* resolves to an existing ACTIVE definition whose
    latest revision already references the same *image*, it is reused.
    Otherwise a new definition (or revision) is registered automatically
    using standard IAM roles and logging configuration read from env vars.

    This removes the need to manually create Batch job definitions when
    on-boarding new analysis scripts – the API creates them on first use.

    Parameters
    ----------
    image : str
        Fully qualified container image URI (e.g. an ECR URI).
    definition_name : str, optional
        Explicit name for the job definition.  When *None*, a name is
        derived from the image repository name.

    Returns
    -------
    str
        The job definition name to use with ``submit_job()``.
    """
    if not definition_name:
        definition_name = _image_to_definition_name(image)

    client = _get_batch_client()

    # Check if an active definition already exists with the right image
    try:
        resp = client.describe_job_definitions(
            jobDefinitionName=definition_name,
            status="ACTIVE",
        )
        definitions = resp.get("jobDefinitions", [])
        if definitions:
            latest = max(definitions, key=lambda d: d["revision"])
            current_image = latest.get("containerProperties", {}).get("image", "")
            if current_image == image:
                logger.info(
                    "[BATCH] Reusing job definition %s (rev %d)",
                    definition_name,
                    latest["revision"],
                )
                return definition_name
            logger.info(
                "[BATCH] Image changed for %s (%s -> %s), registering new revision",
                definition_name,
                current_image,
                image,
            )
    except Exception:
        logger.info(
            "[BATCH] No existing definition found for %s, will create",
            definition_name,
        )

    # Register new definition (or new revision of existing name)
    container_props = {
        "image": image,
        "resourceRequirements": [
            {"type": "VCPU", "value": BATCH_DEFAULT_VCPUS},
            {"type": "MEMORY", "value": BATCH_DEFAULT_MEMORY_MIB},
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": "/aws/batch/job",
                "awslogs-region": AWS_REGION,
                "awslogs-stream-prefix": definition_name,
            },
        },
    }
    if BATCH_JOB_ROLE_ARN:
        container_props["jobRoleArn"] = BATCH_JOB_ROLE_ARN
    if BATCH_EXECUTION_ROLE_ARN:
        container_props["executionRoleArn"] = BATCH_EXECUTION_ROLE_ARN

    resp = client.register_job_definition(
        jobDefinitionName=definition_name,
        type="container",
        containerProperties=container_props,
        retryStrategy={"attempts": 1},
        timeout={"attemptDurationSeconds": DEFAULT_TIMEOUT_SECONDS},
    )
    logger.info("[BATCH] Registered %s revision %d", definition_name, resp["revision"])
    return definition_name


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
    resource_overrides=None,
):
    """Submit a single AWS Batch job for an execution.

    This is the simplest dispatch mode: one execution → one Batch job.
    The container runs its default command unless *command* is provided.

    Parameters
    ----------
    resource_overrides : list[dict], optional
        AWS Batch ``resourceRequirements`` entries to override the job
        definition defaults (e.g. vCPUs, memory) at submit time.

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
    if resource_overrides:
        overrides["resourceRequirements"] = resource_overrides

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
    resource_overrides=None,
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

    resource_overrides : list[dict], optional
        Default ``resourceRequirements`` overrides applied to all steps.

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
        if resource_overrides:
            overrides["resourceRequirements"] = resource_overrides

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
# Helpers for user-visible ExecutionLog entries
# ---------------------------------------------------------------------------


def _add_log(execution_id, text, level="INFO"):
    """Create an ExecutionLog entry visible to the user via the API."""
    try:
        log_entry = ExecutionLog(
            text=text,
            level=level,
            execution_id=execution_id,
        )
        db.session.add(log_entry)
    except Exception:
        logger.warning(
            "[BATCH] Could not write ExecutionLog for %s: %s",
            execution_id,
            text,
        )


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
    import datetime
    import traceback as tb

    logger.info("[BATCH] Starting batch_run for execution %s", execution_id)

    execution = Execution.query.get(execution_id)
    if not execution:
        logger.error("[BATCH] Execution %s not found — cannot proceed", execution_id)
        rollbar.report_message(
            f"batch_run: Execution {execution_id} not found", level="error"
        )
        return

    try:
        execution.status = "READY"
        db.session.add(execution)
        db.session.commit()
        logger.info("[BATCH] Execution %s → READY", execution_id)

        # ---- resolve Batch settings ----
        batch_overrides = params.get("batch", {})
        script = Script.query.get(execution.script_id) if execution.script_id else None

        job_queue = (
            batch_overrides.get("job_queue")
            or (getattr(script, "batch_job_queue", None) if script else None)
            or DEFAULT_BATCH_JOB_QUEUE
        )

        # Resolve the full container image URI.  Priority:
        #   1. params["batch"]["image"]  (per-execution override)
        #   2. script.batch_image        (set via configuration.json)
        #   3. {ECR_REGISTRY}/{slug}:latest  (auto-constructed)
        #   4. the raw slug passed in ``image`` (legacy fallback)
        resolved_image = batch_overrides.get("image") or (
            getattr(script, "batch_image", None) if script else None
        )
        if not resolved_image and ECR_REGISTRY:
            resolved_image = f"{ECR_REGISTRY}/{image}:latest"
        if not resolved_image:
            resolved_image = image
            logger.warning(
                "[BATCH] Execution %s: using raw slug %r as image — "
                "set ECR_REGISTRY or script.batch_image for a "
                "fully qualified ECR URI",
                execution_id,
                image,
            )

        # If the resolved image is a short form (repo:tag without registry
        # prefix, i.e. no dots in the hostname), prepend the ECR registry.
        if ECR_REGISTRY and "." not in resolved_image.split("/")[0]:
            resolved_image = f"{ECR_REGISTRY}/{resolved_image}"

        # Job definition: resolve explicit name (if any), then auto-ensure
        # the definition exists in AWS Batch for the execution's image.
        explicit_definition = batch_overrides.get("job_definition") or (
            getattr(script, "batch_job_definition", None) if script else None
        )
        job_definition = _ensure_job_definition(
            resolved_image, definition_name=explicit_definition
        )

        timeout = batch_overrides.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        command = batch_overrides.get("command")

        # Resource overrides – let callers tune vCPUs/memory per-execution
        # without needing a separate job definition.
        resource_overrides = None
        if batch_overrides.get("vcpus") or batch_overrides.get("memory_mib"):
            resource_overrides = []
            if batch_overrides.get("vcpus"):
                resource_overrides.append(
                    {"type": "VCPU", "value": str(batch_overrides["vcpus"])}
                )
            if batch_overrides.get("memory_mib"):
                resource_overrides.append(
                    {"type": "MEMORY", "value": str(batch_overrides["memory_mib"])}
                )

        logger.info(
            "[BATCH] Execution %s: resolved image=%s, job_queue=%s, "
            "job_definition=%s, timeout=%ss, command=%s, "
            "resource_overrides=%s, script=%s (compute_type=%s)",
            execution_id,
            resolved_image,
            job_queue,
            job_definition,
            timeout,
            command or "(default)",
            resource_overrides or "(default)",
            getattr(script, "slug", "?") if script else "none",
            getattr(script, "compute_type", "?") if script else "none",
        )

        # User-visible log: about to upload params
        _add_log(execution.id, "Preparing execution parameters for upload to S3")

        # ---- push params to S3 ----
        config_s3_uri = push_params_to_s3(params, str(execution_id))
        logger.info("[BATCH] Params uploaded to %s", config_s3_uri)
        _add_log(execution.id, "Parameters uploaded — submitting to AWS Batch")

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
                resource_overrides=resource_overrides,
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
                resource_overrides=resource_overrides,
            )

        logger.info(
            "[BATCH] Execution %s: submitted jobs=%s, queue=%s, definition=%s",
            execution_id,
            job_ids,
            job_queue,
            job_definition,
        )

        # User-visible log: job submitted
        _add_log(
            execution.id,
            f"Batch job submitted (queue={job_queue}, definition={job_definition})",
        )

        # Store Batch job IDs for tracking; final results will overwrite.
        execution.results = {"batch_jobs": job_ids, "status": "SUBMITTED"}
        execution.status = "RUNNING"
        db.session.add(execution)
        db.session.commit()
        logger.info(
            "[BATCH] Execution %s → RUNNING (Batch jobs dispatched)",
            execution_id,
        )

    except Exception as exc:
        error_msg = str(exc)
        full_tb = tb.format_exc()
        logger.error(
            "[BATCH] Failed to submit for %s: %s\n%s",
            execution_id,
            error_msg,
            full_tb,
        )
        rollbar.report_exc_info()
        try:
            execution.status = "FAILED"
            execution.end_date = datetime.datetime.utcnow()
            execution.results = {
                "error": f"Batch submission failed: {error_msg}",
                "error_type": type(exc).__name__,
                "traceback": full_tb,
                "retry_attempt": self.request.retries,
            }
            _add_log(
                execution.id,
                f"Batch submission failed: {error_msg}",
                level="ERROR",
            )
            db.session.add(execution)
            db.session.commit()
        except Exception as inner:
            logger.error(
                "[BATCH] Could not mark execution %s as FAILED: %s",
                execution_id,
                inner,
            )
        raise self.retry(exc=exc) from None
