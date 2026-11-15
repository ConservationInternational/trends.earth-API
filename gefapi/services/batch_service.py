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

from decimal import ROUND_CEILING, Decimal, InvalidOperation
import gzip
import json
import logging
import os
from pathlib import Path
import re
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


def get_nextflow_driver_config(script):
    """Resolve the driver used for new submissions without AWS lookups."""
    job_definition = getattr(script, "batch_job_definition", None) or os.getenv(
        "NEXTFLOW_DRIVER_JOB_DEFINITION",
        SETTINGS.get("NEXTFLOW_DRIVER_JOB_DEFINITION", ""),
    )
    if not re.fullmatch(
        r"arn:[^:]+:batch:[^:]+:\d{12}:job-definition/[^:]+:\d+", job_definition
    ):
        raise ValueError("NEXTFLOW_DRIVER_JOB_DEFINITION must be a pinned Batch ARN")

    job_queue = getattr(script, "batch_job_queue", None) or os.getenv(
        "NEXTFLOW_DRIVER_JOB_QUEUE", SETTINGS.get("NEXTFLOW_DRIVER_JOB_QUEUE", "")
    )
    if not job_queue:
        raise ValueError("NEXTFLOW_DRIVER_JOB_QUEUE is required")
    return {"job_definition": job_definition, "job_queue": job_queue}


def _nextflow_config(script, image_override=None):
    """Load trusted Nextflow infrastructure settings and pin its analysis image."""
    enabled = (
        os.getenv(
            "NEXTFLOW_ENABLED", str(SETTINGS.get("NEXTFLOW_ENABLED", False))
        ).lower()
        == "true"
    )
    if not enabled:
        raise ValueError("Nextflow execution is not enabled")

    driver = get_nextflow_driver_config(script)
    work_root = os.getenv("NEXTFLOW_WORK_ROOT", SETTINGS.get("NEXTFLOW_WORK_ROOT", ""))
    state_root = os.getenv(
        "NEXTFLOW_STATE_ROOT", SETTINGS.get("NEXTFLOW_STATE_ROOT", "")
    )
    for name, uri in (
        ("NEXTFLOW_WORK_ROOT", work_root),
        ("NEXTFLOW_STATE_ROOT", state_root),
    ):
        if not re.fullmatch(r"s3://[^/]+(?:/[^?#]*)?", uri):
            raise ValueError(f"{name} must be an s3:// URI")
    if not PARAMS_S3_BUCKET:
        raise ValueError("PARAMS_S3_BUCKET is required for Nextflow execution")

    image = (
        image_override
        or os.getenv("NEXTFLOW_TASK_IMAGE")
        or getattr(script, "batch_image", None)
    )
    if not image:
        raise ValueError("A trusted Nextflow analysis image is required")
    if ".dkr.ecr." not in image.split("/", 1)[0]:
        if ".dkr.ecr." not in ECR_REGISTRY:
            raise ValueError("Nextflow analysis image must be hosted in ECR")
        image = f"{ECR_REGISTRY.rstrip('/')}/{image.lstrip('/')}"
    if image_override:
        if "@sha256:" not in image or not re.fullmatch(
            r"[^/]+\.dkr\.ecr\.[^/]+\.amazonaws\.com(?:\.cn)?/"
            r".+@sha256:[0-9a-f]{64}",
            image,
        ):
            raise ValueError("Stored Nextflow image is not a pinned ECR digest")
    else:
        image = _resolve_ecr_image_digest(image)
    return {
        **driver,
        "work_root": work_root.rstrip("/"),
        "state_root": state_root.rstrip("/"),
        "image": image,
        "max_hours": os.getenv("NEXTFLOW_MAX_HOURS", ""),
    }


def _nextflow_driver_timeout_seconds(max_hours):
    """Keep the Batch driver alive through its configured workflow budget."""
    if not max_hours:
        return None
    try:
        hours = Decimal(str(max_hours))
    except InvalidOperation as exc:
        raise ValueError("NEXTFLOW_MAX_HOURS must be a positive number") from exc
    if not hours.is_finite() or hours <= 0:
        raise ValueError("NEXTFLOW_MAX_HOURS must be a positive number")
    return int((hours * 3600).to_integral_value(rounding=ROUND_CEILING)) + 900


def _resolve_ecr_image_digest(image):
    """Return a trusted ECR image pinned by immutable digest."""
    if "@sha256:" in image:
        registry, digest = image.split("@sha256:", 1)
        if not registry or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Nextflow analysis image digest is invalid")
        return image

    registry, _, repository_tag = image.partition("/")
    if ".dkr.ecr." not in registry:
        raise ValueError("Nextflow analysis image must be hosted in ECR")
    repository, separator, tag = repository_tag.rpartition(":")
    if not separator:
        repository, tag = repository_tag, "latest"
    if not repository or not tag:
        raise ValueError("Nextflow analysis image repository or tag is invalid")
    ecr = boto3.client("ecr", region_name=AWS_REGION)
    response = ecr.describe_images(
        repositoryName=repository,
        imageIds=[{"imageTag": tag}],
    )
    details = response.get("imageDetails", [])
    digest = details[0].get("imageDigest") if details else None
    if not digest or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Unable to resolve Nextflow analysis image to an ECR digest")
    return f"{registry}/{repository}@{digest}"


def _s3_location(uri):
    """Split a trusted S3 URI into bucket and key."""
    match = re.fullmatch(r"s3://([^/]+)(?:/(.*))?", uri)
    if not match:
        raise ValueError("Invalid S3 URI")
    return match.group(1), match.group(2) or ""


def _nextflow_state_location(execution_id, state_uri=None):
    root = state_uri or _nextflow_config_for_state()
    bucket, key = _s3_location(root)
    if not state_uri:
        key = f"{key.rstrip('/')}/{execution_id}"
    return bucket, key.strip("/")


def _nextflow_config_for_state():
    root = os.getenv("NEXTFLOW_STATE_ROOT", SETTINGS.get("NEXTFLOW_STATE_ROOT", ""))
    if not root:
        return ""
    return root.rstrip("/")


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


def submit_nextflow_driver(
    execution_id,
    params_s3_uri,
    script,
    *,
    resume=False,
    previous_metadata=None,
):
    """Submit the publisher-approved Nextflow driver with server-owned settings."""
    previous_metadata = previous_metadata or {}
    config = _nextflow_config(
        script,
        image_override=previous_metadata.get("image") if resume else None,
    )
    work_uri = (
        previous_metadata.get("work_uri") if resume else None
    ) or f"{config['work_root']}/{execution_id}"
    state_uri = (
        previous_metadata.get("state_uri") if resume else None
    ) or f"{config['state_root']}/{execution_id}"
    job_definition = (
        previous_metadata.get("driver_job_definition") if resume else None
    ) or config["job_definition"]
    job_queue = (
        previous_metadata.get("driver_job_queue") if resume else None
    ) or config["job_queue"]
    params_bucket = (
        previous_metadata.get("params_s3_bucket") if resume else None
    ) or PARAMS_S3_BUCKET
    params_prefix = (
        previous_metadata.get("params_s3_prefix") if resume else None
    ) or PARAMS_S3_PREFIX
    max_hours = (previous_metadata.get("max_hours") if resume else None) or config[
        "max_hours"
    ]
    driver_timeout_seconds = _nextflow_driver_timeout_seconds(max_hours)
    env = {
        "EXECUTION_ID": str(execution_id),
        "PARAMS_S3_BUCKET": params_bucket,
        "PARAMS_S3_PREFIX": params_prefix,
        "AWS_REGION": AWS_REGION,
        "NEXTFLOW_TASK_IMAGE": config["image"],
        "NEXTFLOW_WORK_URI": work_uri,
        "NEXTFLOW_STATE_URI": state_uri,
        "NEXTFLOW_RESUME": "true" if resume else "false",
        "NEXTFLOW_MAX_HOURS": max_hours,
    }
    job_name = f"te-nf-{str(execution_id)[:8]}"
    submit_kwargs = {
        "jobName": job_name,
        "jobQueue": job_queue,
        "jobDefinition": job_definition,
        "containerOverrides": {
            "environment": [
                {"name": name, "value": str(value)}
                for name, value in env.items()
                if value != ""
            ],
        },
        "retryStrategy": {"attempts": 1},
        "tags": {"execution_id": str(execution_id)},
        "propagateTags": True,
    }
    if driver_timeout_seconds is not None:
        submit_kwargs["timeout"] = {"attemptDurationSeconds": driver_timeout_seconds}
    response = _get_batch_client().submit_job(**submit_kwargs)
    job_id = response["jobId"]
    return {
        "batch_jobs": {"nextflow": job_id},
        "status": "SUBMITTED",
        "nextflow": {
            "driver_job_id": job_id,
            "params_s3_uri": params_s3_uri,
            "work_uri": work_uri,
            "state_uri": state_uri,
            "image": config["image"],
            "params_s3_bucket": params_bucket,
            "params_s3_prefix": params_prefix,
            "driver_job_definition": job_definition,
            "driver_job_queue": job_queue,
            "max_hours": max_hours,
            "driver_timeout_seconds": driver_timeout_seconds,
            "resume": resume,
            "phase": "SUBMITTED",
        },
    }


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


def _nextflow_child_job_ids(execution_id, execution=None, client=None, state_uri=None):
    """Find Nextflow children through the state manifest and execution tag."""
    execution = execution or Execution.query.get(execution_id)
    if not execution:
        return [], [f"Execution {execution_id} not found"]

    metadata = (execution.results or {}).get("nextflow") or {}
    driver_id = ((execution.results or {}).get("batch_jobs") or {}).get("nextflow")
    job_ids = set()
    errors = []

    try:
        bucket, state_key = _nextflow_state_location(
            execution_id, state_uri or metadata.get("state_uri")
        )
        response = _get_s3_client().get_object(
            Bucket=bucket, Key=f"{state_key.rstrip('/')}/jobs.json"
        )
        manifest = json.loads(response["Body"].read().decode("utf-8"))
        if isinstance(manifest, dict):
            manifest = manifest.get("jobs", [])
        if isinstance(manifest, list):
            for entry in manifest:
                job_id = (
                    entry
                    if isinstance(entry, str)
                    else entry.get("jobId", entry.get("job_id"))
                    if isinstance(entry, dict)
                    else None
                )
                if isinstance(job_id, str) and job_id and job_id != driver_id:
                    job_ids.add(job_id)
        else:
            errors.append("Nextflow jobs manifest must contain a list")
    except Exception as exc:
        error_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if error_code not in {"NoSuchKey", "404"}:
            errors.append(f"Could not read Nextflow jobs manifest: {exc}")

    batch = client or _get_batch_client()
    queues = list(
        dict.fromkeys(
            queue.strip()
            for queue in os.getenv(
                "NEXTFLOW_CHILD_JOB_QUEUES",
                SETTINGS.get("NEXTFLOW_CHILD_JOB_QUEUES", ""),
            ).split(",")
            if queue.strip()
        )
    )
    if not queues:
        try:
            for page in batch.get_paginator("describe_job_queues").paginate():
                queues.extend(
                    queue["jobQueueArn"] for queue in page.get("jobQueues", [])
                )
        except Exception as exc:
            errors.append(f"Could not discover Nextflow child job queues: {exc}")
    candidates = set()
    for queue in queues:
        for status in _BATCH_CANCELLABLE:
            kwargs = {"jobStatus": status, "maxResults": 1000}
            if queue:
                kwargs["jobQueue"] = queue
            token = None
            seen_tokens = set()
            while True:
                if token:
                    kwargs["nextToken"] = token
                try:
                    response = batch.list_jobs(**kwargs)
                except Exception as exc:
                    errors.append(f"Could not list Nextflow child jobs: {exc}")
                    break
                candidates.update(
                    summary["jobId"]
                    for summary in response.get("jobSummaryList", [])
                    if summary.get("jobId") and summary["jobId"] != driver_id
                )
                token = response.get("nextToken")
                if not token or token in seen_tokens:
                    break
                seen_tokens.add(token)
            kwargs.pop("nextToken", None)

    candidate_ids = sorted(candidates - job_ids)
    for index in range(0, len(candidate_ids), 100):
        try:
            response = batch.describe_jobs(jobs=candidate_ids[index : index + 100])
            for job in response.get("jobs", []):
                if (job.get("tags") or {}).get("execution_id") == str(execution_id):
                    job_ids.add(job["jobId"])
        except Exception as exc:
            errors.append(f"Could not inspect Nextflow job tags: {exc}")
    return sorted(job_ids), errors


def terminate_nextflow_children(execution_id, state_uri=None):
    """Terminate orphaned child jobs and confirm none remain active."""
    result = {
        "jobs_terminated": [],
        "active_job_ids": [],
        "errors": [],
        "cleaned": False,
    }
    execution = Execution.query.get(execution_id)
    if not execution:
        result["errors"].append(f"Execution {execution_id} not found")
        return result
    client = _get_batch_client()
    job_ids, errors = _nextflow_child_job_ids(
        execution_id,
        execution=execution,
        client=client,
        state_uri=state_uri,
    )
    result["errors"].extend(errors)
    described = {}
    for index in range(0, len(job_ids), 100):
        try:
            response = client.describe_jobs(jobs=job_ids[index : index + 100])
            described.update({job["jobId"]: job for job in response.get("jobs", [])})
        except Exception as exc:
            result["errors"].append(f"Could not describe Nextflow child jobs: {exc}")

    for job_id in job_ids:
        job = described.get(job_id)
        if not job or job.get("status") not in _BATCH_CANCELLABLE:
            continue
        try:
            client.terminate_job(
                jobId=job_id, reason="Nextflow driver failed; cleaning up children"
            )
            result["jobs_terminated"].append(job_id)
        except Exception as exc:
            result["errors"].append(f"Could not terminate child job {job_id}: {exc}")

    for index in range(0, len(job_ids), 100):
        try:
            response = client.describe_jobs(jobs=job_ids[index : index + 100])
            result["active_job_ids"].extend(
                job["jobId"]
                for job in response.get("jobs", [])
                if job.get("status") in _BATCH_CANCELLABLE
            )
        except Exception as exc:
            result["errors"].append(f"Could not verify Nextflow child cleanup: {exc}")
    result["cleaned"] = not result["active_job_ids"] and not result["errors"]
    return result


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

    execution_results = execution.results or {}
    batch_jobs = execution_results.get("batch_jobs")
    nextflow = (execution.params or {}).get("workflow_backend") == "nextflow"
    client = None

    if nextflow:
        try:
            bucket, state_key = _nextflow_state_location(
                execution_id,
                (execution_results.get("nextflow") or {}).get("state_uri"),
            )
            state_key = state_key.rstrip("/")
            client_s3 = _get_s3_client()
            client_s3.put_object(
                Bucket=bucket,
                Key=f"{state_key}/cancelled.json",
                Body=json.dumps({"execution_id": str(execution_id)}).encode(),
                ContentType="application/json",
            )
            result["cancellation_marker_written"] = True
        except Exception as exc:
            result["errors"].append(
                f"Could not write Nextflow cancellation marker: {exc}"
            )

        try:
            client = _get_batch_client()
            child_ids, errors = _nextflow_child_job_ids(
                execution_id,
                execution=execution,
                client=client,
                state_uri=(execution_results.get("nextflow") or {}).get("state_uri"),
            )
            result["errors"].extend(errors)
            batch_jobs = dict(batch_jobs or {})
            batch_jobs.update(
                {
                    f"nextflow-child-{index}": job_id
                    for index, job_id in enumerate(child_ids)
                }
            )
        except Exception as exc:
            result["errors"].append(f"Could not discover Nextflow child jobs: {exc}")

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

    client = client or _get_batch_client()

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
def batch_run(self, execution_id, image=None, environment=None, params=None):
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

    1. ``params["batch"]`` values approved by the script's execution policy
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
        if image is None:
            from gefapi.services.execution_service import load_execution_context

            image, environment, params = load_execution_context(execution_id, "batch")
        from gefapi.utils.batch_policy import (
            batch_workflow_backend,
            validate_batch_execution_params,
        )

        script = Script.query.get(execution.script_id) if execution.script_id else None
        validate_batch_execution_params(
            params,
            script,
            default_queue=DEFAULT_BATCH_JOB_QUEUE,
            default_definition=DEFAULT_BATCH_JOB_DEFINITION,
        )
        backend = batch_workflow_backend(params, script)
        if backend is not None:
            params = {**params, "workflow_backend": backend}
        nextflow_execution = bool(
            params and params.get("workflow_backend") == "nextflow"
        )
        if nextflow_execution:
            execution = (
                db.session.query(Execution)
                .filter(Execution.id == execution_id)
                .with_for_update()
                .populate_existing()
                .first()
            )
            if execution is not None:
                execution.params = params
        if not execution or execution.status in {
            "CANCELLING",
            "CANCELLED",
            "FINISHED",
            "FAILED",
        }:
            logger.info("[BATCH] Execution %s is not dispatchable", execution_id)
            return
        prior_results = execution.results or {}
        prior_nextflow = prior_results.get("nextflow") or {}
        if (
            nextflow_execution
            and prior_results.get("batch_jobs", {}).get("nextflow")
            and not prior_nextflow.get("resume_requested")
        ):
            logger.info(
                "[BATCH] Nextflow driver for %s is already recorded; "
                "skipping duplicate dispatch",
                execution_id,
            )
            return
        execution.status = "READY"
        db.session.add(execution)
        if not nextflow_execution:
            db.session.commit()
        logger.info("[BATCH] Execution %s → READY", execution_id)

        batch_overrides = params.get("batch", {})

        if params.get("workflow_backend") == "nextflow":
            resume = bool(prior_nextflow.get("resume_requested"))
            params_s3_uri = prior_nextflow.get("params_s3_uri")
            if resume and not params_s3_uri:
                raise ValueError("Original Nextflow params S3 location is missing")
            if not resume:
                params_s3_uri = push_params_to_s3(params, str(execution_id))
                prior_nextflow = dict(prior_nextflow)
                prior_nextflow.update(
                    {
                        "params_s3_uri": params_s3_uri,
                        "params_s3_bucket": PARAMS_S3_BUCKET,
                        "params_s3_prefix": PARAMS_S3_PREFIX,
                    }
                )
                staged_results = dict(execution.results or {})
                staged_results["nextflow"] = prior_nextflow
                execution.results = staged_results
            db.session.flush()
            submitted = submit_nextflow_driver(
                str(execution_id),
                params_s3_uri,
                script,
                resume=resume,
                previous_metadata=prior_nextflow,
            )
            nextflow_metadata = dict(prior_nextflow)
            nextflow_metadata.update(submitted["nextflow"])
            nextflow_metadata["resume_requested"] = False
            nextflow_metadata.pop("error", None)
            nextflow_metadata.pop("success", None)
            nextflow_metadata.pop("child_cleanup", None)
            submitted["nextflow"] = nextflow_metadata

            db.session.refresh(execution)
            if execution.status == "CANCELLING":
                current = dict(execution.results or {})
                current["batch_jobs"] = submitted["batch_jobs"]
                current["nextflow"] = nextflow_metadata
                execution.results = current
                db.session.add(execution)
                db.session.commit()
                _get_batch_client().terminate_job(
                    jobId=submitted["batch_jobs"]["nextflow"],
                    reason="Execution cancellation requested during dispatch",
                )
                return

            execution.results = submitted
            execution.status = "RUNNING"
            db.session.add(execution)
            _add_log(execution.id, "Nextflow workflow driver submitted to AWS Batch")
            db.session.commit()
            return

        job_queue = (
            batch_overrides.get("job_queue")
            or (getattr(script, "batch_job_queue", None) if script else None)
            or DEFAULT_BATCH_JOB_QUEUE
        )

        # An explicit image has already been checked against the published image.
        # Resolve the full container image URI.  Priority:
        #   1. params["batch"]["image"]  (same published image)
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
            if params and params.get("workflow_backend") == "nextflow":
                preserved = dict(execution.results or {})
                metadata = dict(preserved.get("nextflow") or {})
                metadata.update(
                    {
                        "phase": "FAILED",
                        "error": error_msg,
                        "resume_requested": False,
                    }
                )
                preserved["nextflow"] = metadata
                preserved["error"] = f"Nextflow submission failed: {error_msg}"
                execution.results = preserved
            else:
                execution.results = {
                    "error": f"Batch submission failed: {error_msg}",
                    "error_type": type(exc).__name__,
                    "traceback": full_tb,
                    "retry_attempt": self.request.retries,
                }
            execution.status = "FAILED"
            execution.end_date = datetime.datetime.now(datetime.UTC)
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
        if params and params.get("workflow_backend") == "nextflow":
            return
        raise self.retry(exc=exc) from None
