"""Authorize executable Batch settings independently of analysis parameters."""

import logging
import re

from gefapi.errors import InvalidExecutionParameters

logger = logging.getLogger(__name__)


def _reject(message):
    logger.warning("[SECURITY]: Rejected Batch execution parameters: %s", message)
    raise InvalidExecutionParameters(message=message)


def validate_batch_policy(policy):
    """Validate a policy supplied by a trusted script publisher."""
    if not isinstance(policy, dict):
        _reject("batch_execution_config must be an object")
    allowed_keys = {
        "allowed_commands",
        "allowed_job_queues",
        "allowed_job_definitions",
    }
    if policy.keys() - allowed_keys:
        _reject("Unknown batch_execution_config fields")
    commands = policy.get("allowed_commands", [])
    if not isinstance(commands, list) or any(
        not isinstance(command, list)
        or not command
        or any(not isinstance(arg, str) or not arg for arg in command)
        for command in commands
    ):
        _reject("allowed_commands must be a list of nonempty string arrays")
    for key in ("allowed_job_queues", "allowed_job_definitions"):
        values = policy.get(key, [])
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value for value in values
        ):
            _reject(f"{key} must be a list of nonempty strings")


def _command_allowed(command, allowed):
    if not isinstance(command, list) or not command:
        return False
    if any(not isinstance(arg, str) for arg in command):
        return False
    return any(
        len(command) == len(template)
        and all(
            re.fullmatch(re.escape(pattern).replace(r"\{index\}", "[0-9]+"), argument)
            for argument, pattern in zip(command, template, strict=True)
        )
        for template in allowed
    )


def validate_batch_execution_params(
    params, script, *, default_queue=None, default_definition=None
):
    """Keep resource tuning while limiting commands/infrastructure to script policy."""
    if not isinstance(params, dict):
        _reject("Execution parameters must be an object")
    policy = getattr(script, "batch_execution_config", None) or {}
    validate_batch_policy(policy)
    batch = params.get("batch", {})
    if not isinstance(batch, dict):
        _reject("batch must be an object")
    if "image" in batch and batch["image"] != getattr(script, "batch_image", None):
        _reject("Batch image must match the published script image")
    queues = [
        getattr(script, "batch_job_queue", None) or default_queue,
        *policy.get("allowed_job_queues", []),
    ]
    definitions = [
        getattr(script, "batch_job_definition", None) or default_definition,
        *policy.get("allowed_job_definitions", []),
    ]

    def validate_overrides(overrides):
        if "command" in overrides and not _command_allowed(
            overrides["command"], policy.get("allowed_commands", [])
        ):
            _reject("Batch command is not approved by the script publisher")
        for key, allowed in (("job_queue", queues), ("job_definition", definitions)):
            if key in overrides and (
                not isinstance(overrides[key], str) or overrides[key] not in allowed
            ):
                _reject(f"Batch {key} is not approved by the script publisher")

    validate_overrides(batch)
    if "pipeline" in params:
        steps = params["pipeline"]
        if not isinstance(steps, list) or not steps:
            _reject("pipeline must be a nonempty array")
        for step in steps:
            if not isinstance(step, dict):
                _reject("Each pipeline step must be an object")
            if not isinstance(step.get("name"), str) or not step["name"]:
                _reject("Each pipeline step requires a nonempty name")
            if "image" in step:
                _reject("Pipeline images cannot be overridden")
            validate_overrides(step)
