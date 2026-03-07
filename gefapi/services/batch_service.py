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

Batch override block
~~~~~~~~~~~~~~~~~~~~
Callers may include a ``params["batch"]`` dict to customise how the
job is submitted.  Supported keys:

* ``job_queue`` (str) – AWS Batch job queue name.
* ``job_definition`` (str) – AWS Batch job definition name.
* ``image`` (str) – fully-qualified container image URI.
* ``timeout_seconds`` (int) – per-job attempt duration in seconds.
* ``command`` (list[str]) – container command override (single-job
  mode only).
* ``vcpus`` (int) – vCPU resource override.
* ``memory_mib`` (int) – memory resource override in MiB.
* ``tags`` (dict[str, str]) – AWS cost-allocation tags applied to
  every submitted Batch job **and** to the S3 parameter object.
  Tags are also propagated to ECS tasks.  Example::

      "batch": {
          "tags": {"Project": "avoided-emissions"}
      }

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

# Automatic retry on spot instance termination.  AWS Batch retries
# individual array children independently, so only the interrupted
# portions of an array job are re-run.
BATCH_SPOT_RETRY_ATTEMPTS = int(os.getenv("BATCH_SPOT_RETRY_ATTEMPTS", "3"))

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


def _get_logs_client():
    return boto3.client("logs", region_name=AWS_REGION)


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

        # Apply cost-allocation tags from the params["batch"]["tags"] dict
        # if the caller provided them.  This lets downstream apps like
        # avoided-emissions tag S3 objects without the API hard-coding
        # project-specific values.
        extra_args = {}
        cost_tags = (params_dict.get("batch") or {}).get("tags")
        if cost_tags and isinstance(cost_tags, dict):
            from urllib.parse import quote

            extra_args["Tagging"] = "&".join(
                f"{quote(k)}={quote(v)}" for k, v in cost_tags.items()
            )
        _get_s3_client().upload_file(
            str(gz_path),
            PARAMS_S3_BUCKET,
            key,
            ExtraArgs=extra_args if extra_args else None,
        )
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


def _build_spot_retry_strategy(attempts=None):
    """Build an AWS Batch retry strategy that retries on spot termination.

    When a Spot instance is reclaimed, AWS Batch reports "Host EC2
    (instance …) terminated" as the status reason.  The
    ``evaluateOnExit`` rules below instruct Batch to automatically
    retry those attempts while letting genuine application failures
    propagate immediately.

    For **array jobs** Batch retries each child independently, so only
    the children that were interrupted are re-run — completed children
    are not affected.

    Parameters
    ----------
    attempts : int, optional
        Maximum number of attempts (including the first try).  Defaults
        to ``BATCH_SPOT_RETRY_ATTEMPTS`` (env var, default 3).
    """
    max_attempts = attempts if attempts is not None else BATCH_SPOT_RETRY_ATTEMPTS
    # Clamp to the AWS Batch maximum of 10 attempts
    max_attempts = max(1, min(max_attempts, 10))

    return {
        "attempts": max_attempts,
        "evaluateOnExit": [
            # Spot reclamation – always retry
            {
                "onStatusReason": "Host EC2*",
                "action": "RETRY",
            },
            # Catch-all: do NOT retry for normal failures
            {
                "onExitCode": "*",
                "action": "EXIT",
            },
        ],
    }


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
        retryStrategy=_build_spot_retry_strategy(),
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
    tags=None,
):
    """Submit a single AWS Batch job for an execution.

    This is the simplest dispatch mode: one execution → one Batch job.
    The container runs its default command unless *command* is provided.

    Parameters
    ----------
    execution_id : str
        UUID of the execution (used to build the job name and env vars).
    config_s3_uri : str
        ``s3://`` URI of the compressed JSON parameters file.
    environment : dict
        Key/value pairs injected as container environment variables.
    job_queue : str, optional
        AWS Batch job queue.  Falls back to ``DEFAULT_BATCH_JOB_QUEUE``.
    job_definition : str, optional
        AWS Batch job definition.  Falls back to
        ``DEFAULT_BATCH_JOB_DEFINITION``.
    command : list[str], optional
        Container command override.
    timeout_seconds : int, optional
        Attempt duration in seconds.  Falls back to
        ``DEFAULT_TIMEOUT_SECONDS``.
    resource_overrides : list[dict], optional
        AWS Batch ``resourceRequirements`` entries to override the job
        definition defaults (e.g. vCPUs, memory) at submit time.
    tags : dict[str, str], optional
        AWS resource tags applied to the Batch job.  When provided,
        ``propagateTags`` is also set so that tags flow to the
        underlying ECS task.  Typically used for cost-allocation
        (e.g. ``{"Project": "avoided-emissions"}``).

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

    submit_kwargs = {
        "jobName": f"te-{execution_id[:8]}",
        "jobQueue": job_queue or DEFAULT_BATCH_JOB_QUEUE,
        "jobDefinition": job_definition or DEFAULT_BATCH_JOB_DEFINITION,
        "containerOverrides": overrides,
        "retryStrategy": _build_spot_retry_strategy(),
        "timeout": {
            "attemptDurationSeconds": timeout_seconds or DEFAULT_TIMEOUT_SECONDS
        },
    }
    if tags:
        submit_kwargs["tags"] = tags
        submit_kwargs["propagateTags"] = True

    resp = client.submit_job(**submit_kwargs)
    return {"job_id": resp["jobId"]}


def _build_step_resources(step, default_overrides):
    """Build ``resourceRequirements`` for a single pipeline step.

    Per-step ``vcpus`` and ``memory_mib`` keys take precedence over the
    global *default_overrides* list.  Returns ``None`` when there are no
    overrides at all.
    """
    step_vcpus = step.get("vcpus")
    step_memory = step.get("memory_mib")

    if step_vcpus or step_memory:
        reqs = []
        if step_vcpus:
            reqs.append({"type": "VCPU", "value": str(step_vcpus)})
        if step_memory:
            reqs.append({"type": "MEMORY", "value": str(step_memory)})
        return reqs

    return default_overrides


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
    tags=None,
):
    """Submit a multi-step pipeline to AWS Batch with inter-step dependencies.

    Parameters
    ----------
    execution_id : str
        UUID of the execution.
    config_s3_uri : str
        ``s3://`` URI of the compressed JSON parameters file.
    environment : dict
        Key/value pairs injected as container environment variables.
    steps : list[dict]
        Each dict may contain:

        * ``name`` (str, required) – human-readable label
        * ``command`` (list[str], optional) – container command override
        * ``array_size`` (int, optional) – submit as an array job
        * ``job_definition`` (str, optional) – per-step override
        * ``job_queue`` (str, optional) – per-step override
        * ``timeout_seconds`` (int, optional) – per-step override
        * ``vcpus`` (int, optional) – per-step vCPU override
        * ``memory_mib`` (int, optional) – per-step memory override (MiB)
        * ``retry_attempts`` (int, optional) – max attempts for spot retries

    job_queue : str, optional
        Default AWS Batch job queue for all steps.
    job_definition : str, optional
        Default AWS Batch job definition for all steps.
    timeout_seconds : int, optional
        Default attempt duration for all steps.
    resource_overrides : list[dict], optional
        Default ``resourceRequirements`` overrides applied to steps that
        do not specify their own ``vcpus``/``memory_mib``.
    tags : dict[str, str], optional
        AWS resource tags applied to every submitted Batch job in the
        pipeline.  When provided, ``propagateTags`` is also set so
        that tags flow to the underlying ECS tasks.  Typically used
        for cost-allocation (e.g. ``{"Project": "avoided-emissions"}``).

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

        # Per-step resource overrides take precedence over the global
        # resource_overrides parameter.
        step_resources = _build_step_resources(step, resource_overrides)
        if step_resources:
            overrides["resourceRequirements"] = step_resources

        step_retry = _build_spot_retry_strategy(
            attempts=step.get("retry_attempts"),
        )

        submit_kwargs = {
            "jobName": f"te-{name}-{execution_id[:8]}",
            "jobQueue": step_queue,
            "jobDefinition": step_def,
            "containerOverrides": overrides,
            "retryStrategy": step_retry,
            "timeout": {"attemptDurationSeconds": step_timeout},
        }
        if tags:
            submit_kwargs["tags"] = tags
            submit_kwargs["propagateTags"] = True

        array_size = step.get("array_size", 0)
        if array_size > 1:
            submit_kwargs["arrayProperties"] = {"size": array_size}

        if prev_job_id:
            # Plain dependency (no "type") means "wait for this job to
            # finish".  For array jobs this waits for ALL children.
            # Note: "type": "SEQUENTIAL" is only valid when BOTH the
            # dependent and the dependency are array jobs.
            submit_kwargs["dependsOn"] = [{"jobId": prev_job_id}]

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
# Job termination (cancellation)
# ---------------------------------------------------------------------------

# AWS Batch statuses that are still active and should be terminated
_BATCH_CANCELLABLE = frozenset(
    {"SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"}
)


def terminate_batch_jobs(execution_id, reason="Cancelled by user"):
    """Terminate all AWS Batch jobs associated with *execution_id*.

    Reads ``execution.results["batch_jobs"]`` to discover the Batch job
    IDs, describes them to check their current status, and calls
    ``TerminateJob`` for any that are still active.

    For pipeline executions this terminates every step; dependent steps
    that are still ``PENDING`` will also be cancelled by AWS Batch when
    their upstream dependency is terminated.

    Parameters
    ----------
    execution_id : str
        UUID of the execution whose Batch jobs should be terminated.
    reason : str, optional
        Human-readable reason passed to the ``TerminateJob`` API.

    Returns
    -------
    dict
        Summary with keys:

        * ``jobs_terminated`` – list of dicts with ``job_id``, ``name``,
          ``previous_status``, and ``success``.
        * ``errors`` – list of error message strings.
    """
    result = {"jobs_terminated": [], "errors": []}

    execution = Execution.query.get(execution_id)
    if not execution:
        result["errors"].append(f"Execution {execution_id} not found")
        return result

    batch_jobs = (execution.results or {}).get("batch_jobs")
    if not batch_jobs:
        logger.info(
            "[BATCH-CANCEL] Execution %s has no batch_jobs in results",
            execution_id,
        )
        return result

    if not isinstance(batch_jobs, dict):
        result["errors"].append(
            f"Unexpected batch_jobs type: {type(batch_jobs).__name__}"
        )
        return result

    client = _get_batch_client()

    # Describe all jobs first to check which are still active
    job_ids = list(batch_jobs.values())
    described: dict = {}
    for i in range(0, len(job_ids), 100):
        chunk = job_ids[i : i + 100]
        try:
            resp = client.describe_jobs(jobs=chunk)
            for job in resp.get("jobs", []):
                described[job["jobId"]] = job
        except Exception as exc:
            error_msg = f"describe_jobs failed: {exc}"
            logger.error("[BATCH-CANCEL] %s", error_msg)
            result["errors"].append(error_msg)

    # Terminate active jobs
    for name, job_id in batch_jobs.items():
        job = described.get(job_id)
        if not job:
            logger.info(
                "[BATCH-CANCEL] Job %s (%s) not found — may have expired",
                job_id,
                name,
            )
            result["jobs_terminated"].append(
                {
                    "job_id": job_id,
                    "name": name,
                    "previous_status": "NOT_FOUND",
                    "success": False,
                }
            )
            continue

        status = job["status"]
        if status not in _BATCH_CANCELLABLE:
            logger.info(
                "[BATCH-CANCEL] Job %s (%s) already in terminal state %s",
                job_id,
                name,
                status,
            )
            result["jobs_terminated"].append(
                {
                    "job_id": job_id,
                    "name": name,
                    "previous_status": status,
                    "success": True,
                }
            )
            continue

        try:
            client.terminate_job(jobId=job_id, reason=reason)
            logger.info(
                "[BATCH-CANCEL] Terminated job %s (%s), was %s",
                job_id,
                name,
                status,
            )
            result["jobs_terminated"].append(
                {
                    "job_id": job_id,
                    "name": name,
                    "previous_status": status,
                    "success": True,
                }
            )
        except Exception as exc:
            error_msg = f"Failed to terminate job {job_id} ({name}): {exc}"
            logger.error("[BATCH-CANCEL] %s", error_msg)
            result["errors"].append(error_msg)
            result["jobs_terminated"].append(
                {
                    "job_id": job_id,
                    "name": name,
                    "previous_status": status,
                    "success": False,
                }
            )

    return result


# ---------------------------------------------------------------------------
# CloudWatch log retrieval
# ---------------------------------------------------------------------------


def get_batch_logs(execution_id):
    """Retrieve CloudWatch logs for all Batch jobs belonging to *execution_id*.

    The function inspects ``execution.results["batch_jobs"]`` to discover
    the AWS Batch job IDs, then uses ``describe_jobs`` to find the
    CloudWatch log stream for each job.  Finally it fetches events from
    those log streams and returns them in a format consistent with the
    Docker-logs endpoint.

    Returns
    -------
    list[dict] | None
        A list of ``{"id": int, "created_at": str, "text": str, "job_name": str}``
        dicts, or ``None`` if no jobs / logs could be found.
    """
    execution = Execution.query.get(execution_id)
    if not execution:
        logger.warning("[BATCH-LOGS] Execution %s not found", execution_id)
        return None

    batch_jobs = (execution.results or {}).get("batch_jobs")
    if not batch_jobs:
        logger.warning(
            "[BATCH-LOGS] Execution %s has no batch_jobs in results", execution_id
        )
        return None

    # batch_jobs is either {"job_id": "xxx"} (single) or
    # {"step_name": "job_id", ...} (pipeline).
    if isinstance(batch_jobs, dict):
        job_entries = list(batch_jobs.items())
    else:
        logger.warning(
            "[BATCH-LOGS] Unexpected batch_jobs type for %s: %s",
            execution_id,
            type(batch_jobs),
        )
        return None

    # Describe jobs to get their log stream names
    job_ids = [jid for _, jid in job_entries]
    batch_client = _get_batch_client()
    described: dict = {}
    for i in range(0, len(job_ids), 100):
        chunk = job_ids[i : i + 100]
        try:
            resp = batch_client.describe_jobs(jobs=chunk)
            for job in resp.get("jobs", []):
                described[job["jobId"]] = job
        except Exception as exc:
            logger.error("[BATCH-LOGS] describe_jobs failed: %s", exc)

    # Fallback: if describe_jobs didn't return a job (e.g. expired after 24h),
    # use log stream names previously saved in batch_statuses by the monitor.
    saved_statuses = (execution.results or {}).get("batch_statuses", {})

    # Collect log events from CloudWatch
    logs_client = _get_logs_client()
    all_events: list[dict] = []

    for step_name, job_id in job_entries:
        job = described.get(job_id)
        if job:
            log_stream = (job.get("container") or {}).get("logStreamName")
        else:
            # Job expired from describe_jobs — try the saved log stream name
            saved = saved_statuses.get(step_name, {})
            log_stream = saved.get("log_stream_name")
            if log_stream:
                logger.info(
                    "[BATCH-LOGS] Using saved log stream for job %s: %s",
                    job_id,
                    log_stream,
                )

        if not log_stream:
            logger.info(
                "[BATCH-LOGS] No logStreamName for job %s (status=%s)",
                job_id,
                job.get("status") if job else "EXPIRED",
            )
            continue

        log_group = "/aws/batch/job"
        try:
            events = _fetch_log_events(logs_client, log_group, log_stream)
            for evt in events:
                all_events.append(
                    {
                        "timestamp": evt["timestamp"],
                        "text": evt.get("message", ""),
                        "job_name": step_name,
                    }
                )
        except Exception as exc:
            logger.error(
                "[BATCH-LOGS] Failed to fetch logs for stream %s: %s",
                log_stream,
                exc,
            )

    if not all_events:
        return None

    # Sort by timestamp and assign sequential IDs
    import datetime

    all_events.sort(key=lambda e: e["timestamp"])
    formatted = []
    for i, evt in enumerate(all_events):
        # Convert epoch-ms to ISO 8601
        ts_seconds = evt["timestamp"] / 1000.0
        created_at = (
            datetime.datetime.fromtimestamp(ts_seconds, tz=datetime.UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
        formatted.append(
            {
                "id": i,
                "created_at": created_at,
                "text": evt["text"],
                "job_name": evt["job_name"],
            }
        )

    return formatted


def _fetch_log_events(logs_client, log_group, log_stream, limit=10000):
    """Page through CloudWatch ``get_log_events`` and return all events.

    Returns up to *limit* events from the given log stream.
    """
    events: list[dict] = []
    kwargs = {
        "logGroupName": log_group,
        "logStreamName": log_stream,
        "startFromHead": True,
        "limit": min(limit, 10000),
    }
    prev_token = None
    while len(events) < limit:
        resp = logs_client.get_log_events(**kwargs)
        batch = resp.get("events", [])
        events.extend(batch)
        next_token = resp.get("nextForwardToken")
        if not batch or next_token == prev_token:
            break
        kwargs["nextToken"] = next_token
        prev_token = next_token
    return events[:limit]


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

    Cost-allocation tags
    ~~~~~~~~~~~~~~~~~~~~
    If ``params["batch"]["tags"]`` contains a ``dict[str, str]``, those
    tags are attached to the S3 parameter object and to every Batch job
    (with ``propagateTags=True`` so they flow to ECS tasks).  This keeps
    the API script-agnostic — callers supply their own tags.  Example::

        params = {
            ...,
            "batch": {
                "job_queue": "my-queue",
                "tags": {"Project": "my-project"},
            },
        }
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
        # Cost-allocation tags: forwarded from the caller's params so
        # that AWS Cost Explorer can attribute Batch spend per project.
        cost_tags = batch_overrides.get("tags")

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
                tags=cost_tags,
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
                tags=cost_tags,
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
