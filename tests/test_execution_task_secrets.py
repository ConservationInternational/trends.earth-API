"""Execution task transport must never contain runtime credentials."""

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import rollbar

from gefapi import celery
from gefapi.celery import handle_task_failure
from gefapi.config import SETTINGS
from gefapi.services.docker_service import docker_run
from gefapi.services.execution_service import (
    ExecutionService,
    _dispatch_execution,
    load_execution_context,
)
from gefapi.tasks.queue_processor import _dispatch_queued_execution


@pytest.mark.standalone
@pytest.mark.parametrize(
    ("compute_type", "target"),
    [
        ("gee", "gefapi.services.execution_service.docker_run"),
        ("batch", "gefapi.services.execution_service.batch_run"),
        ("openeo", "gefapi.services.openeo_service.openeo_run"),
    ],
)
def test_dispatch_messages_and_events_contain_only_execution_id(
    compute_type, target, monkeypatch, caplog
):
    execution_id = str(uuid4())
    marker = "synthetic-credential-marker"
    monkeypatch.setitem(SETTINGS, "ORCHESTRATOR", "docker")
    monkeypatch.setitem(SETTINGS, "environment", {"API_PASSWORD": marker})
    with (
        patch(target) as runner,
        patch.object(ExecutionService, "_build_execution_environment") as build,
        caplog.at_level(logging.DEBUG),
    ):
        _dispatch_execution(execution_id, compute_type)
    runner.delay.assert_called_once_with(execution_id)
    build.assert_not_called()
    message = celery.amqp.create_task_message(
        str(uuid4()),
        target,
        args=runner.delay.call_args.args,
        kwargs=runner.delay.call_args.kwargs,
        create_sent_event=True,
    )
    assert message.body[0] == (execution_id,)
    assert message.headers["argsrepr"] == repr((execution_id,))
    assert message.sent_event["args"] == repr((execution_id,))
    assert "API_PASSWORD" not in json.dumps(message.body)
    assert marker not in repr(message)
    assert "API_PASSWORD" not in caplog.text
    assert marker not in caplog.text


@pytest.mark.standalone
def test_queued_dispatch_does_not_resolve_or_send_credentials():
    execution = SimpleNamespace(id=str(uuid4()), queued_at="queued")
    script = SimpleNamespace(compute_type="batch")
    user = SimpleNamespace(id=str(uuid4()))
    with (
        patch("gefapi.tasks.queue_processor.db"),
        patch("gefapi.services.execution_service._dispatch_execution") as dispatch,
        patch.object(ExecutionService, "_build_execution_environment") as build,
    ):
        _dispatch_queued_execution(execution, user, script)
    assert execution.queued_at is None
    build.assert_not_called()
    dispatch.assert_called_once_with(execution.id, "batch")


@pytest.mark.standalone
@pytest.mark.parametrize("compute_type", ["gee", "batch", "openeo"])
def test_worker_resolves_credentials_and_params_from_database(compute_type):
    execution = SimpleNamespace(
        id=str(uuid4()),
        script_id=str(uuid4()),
        user_id=str(uuid4()),
        params={"analysis": "unchanged"},
    )
    script = SimpleNamespace(compute_type=compute_type, slug="published-image")
    user = SimpleNamespace(is_active=True)
    environment = {
        "API_PASSWORD": "synthetic-password-marker",
        "API_EXECUTION_TOKEN": "synthetic-callback-marker",
        "GEE_OAUTH_REFRESH_TOKEN": "synthetic-gee-marker",
        "OPENEO_CREDENTIALS": "synthetic-openeo-marker",
    }
    with (
        patch.object(ExecutionService, "get_execution", return_value=execution),
        patch(
            "gefapi.services.execution_service.ScriptService.get_script",
            return_value=script,
        ),
        patch(
            "gefapi.services.execution_service.UserService.get_user", return_value=user
        ),
        patch.object(
            ExecutionService, "_build_execution_environment", return_value=environment
        ) as build,
    ):
        assert load_execution_context(execution.id, compute_type) == (
            script.slug,
            environment,
            execution.params,
        )
    build.assert_called_once_with(user, execution.id, script=script)


@pytest.mark.standalone
@pytest.mark.parametrize(
    ("actual_type", "active", "error"),
    [("openeo", True, "compute type changed"), ("gee", False, "owner is disabled")],
)
def test_worker_rechecks_owner_and_compute_type(actual_type, active, error):
    execution = SimpleNamespace(script_id="script", user_id="user")
    with (
        patch.object(ExecutionService, "get_execution", return_value=execution),
        patch(
            "gefapi.services.execution_service.ScriptService.get_script",
            return_value=SimpleNamespace(compute_type=actual_type),
        ),
        patch(
            "gefapi.services.execution_service.UserService.get_user",
            return_value=SimpleNamespace(is_active=active),
        ),
        patch.object(ExecutionService, "_build_execution_environment") as build,
        pytest.raises(ValueError, match=error),
    ):
        load_execution_context("execution-id", "gee")
    build.assert_not_called()


@pytest.mark.standalone
def test_failure_reporting_omits_task_args_and_exception_values():
    marker = "synthetic-credential-marker"
    with patch("gefapi.celery.rollbar.report_message") as report:
        handle_task_failure(
            task_id="test-task",
            args=("id", {"API_PASSWORD": marker}),
            kwargs={"token": marker},
            exception=ValueError(marker),
            einfo=marker,
        )
    assert marker not in repr(report.call_args)
    assert report.call_args.kwargs["extra_data"] == {"task_id": "test-task"}


@pytest.mark.standalone
def test_telemetry_defaults_and_worker_startup():
    assert not celery.conf.task_send_sent_event
    assert not celery.conf.worker_send_task_events
    assert not rollbar.SETTINGS["locals"]["enabled"]
    entrypoint = (Path(__file__).parents[1] / "entrypoint.sh").read_text()
    for line in entrypoint.splitlines():
        if "exec celery" in line:
            assert "--loglevel=DEBUG" not in line
            assert " -E " not in line


@pytest.mark.standalone
def test_batch_retry_retains_only_identifier():
    from celery.exceptions import Retry

    from gefapi.services.batch_service import batch_run

    execution_id = str(uuid4())
    # Celery retries reuse the original request, not the locally resolved secrets.
    with patch.object(batch_run, "apply_async") as publish:
        batch_run.push_request(
            args=(execution_id,), kwargs={}, called_directly=False, retries=0
        )
        try:
            with pytest.raises(Retry):
                batch_run.retry(exc=RuntimeError("submission failed"))
        finally:
            batch_run.pop_request()
    assert publish.call_args.args[0] == (execution_id,)
    assert publish.call_args.args[1] == {}


@pytest.mark.standalone
@pytest.mark.parametrize("legacy", [False, True])
def test_docker_worker_resolves_credentials_locally(legacy, caplog):
    marker = "synthetic-password-marker"
    environment = {"API_PASSWORD": marker}
    execution_id = str(uuid4())
    with (
        patch(
            "gefapi.services.execution_service.load_execution_context",
            return_value=("image", environment, {"analysis": "unchanged"}),
        ) as load,
        patch("gefapi.services.docker_service.get_docker_client"),
        patch("gefapi.services.docker_service.Execution"),
        patch("gefapi.services.docker_service.db"),
        patch("gefapi.services.docker_service.push_params_to_s3"),
        patch(
            "gefapi.services.docker_service.DockerService.run",
            return_value=(True, None),
        ) as run,
        caplog.at_level(logging.DEBUG),
    ):
        if legacy:
            docker_run.run(
                execution_id, "image", environment, {"analysis": "unchanged"}
            )
        else:
            docker_run.run(execution_id)
    if legacy:
        load.assert_not_called()
    else:
        load.assert_called_once_with(execution_id, "gee")
    assert run.call_args.kwargs["environment"] == environment
    assert marker not in caplog.text


@pytest.mark.standalone
def test_failure_debug_logging_does_not_contain_resolved_credentials(caplog):
    marker = "synthetic-password-marker"
    execution_id = str(uuid4())
    with (
        patch(
            "gefapi.services.execution_service.load_execution_context",
            return_value=("image", {"API_PASSWORD": marker}, {}),
        ),
        patch("gefapi.services.docker_service.get_docker_client"),
        patch("gefapi.services.docker_service.Execution"),
        patch("gefapi.services.docker_service.db"),
        patch("gefapi.services.docker_service.push_params_to_s3"),
        patch(
            "gefapi.services.docker_service.DockerService.run",
            side_effect=RuntimeError("submission failed"),
        ),
        patch("gefapi.celery.rollbar.report_message") as report,
        caplog.at_level(logging.DEBUG),
    ):
        result = docker_run.apply(args=(execution_id,), throw=False)
    assert result.failed()
    assert "submission failed" in caplog.text
    assert marker not in caplog.text
    assert marker not in repr(report.call_args_list)


@pytest.mark.standalone
def test_docker_context_failure_marks_execution_failed():
    execution = SimpleNamespace(status="PENDING")
    with (
        patch(
            "gefapi.services.execution_service.load_execution_context",
            side_effect=ValueError("Execution owner is disabled"),
        ),
        patch("gefapi.services.docker_service.get_docker_client"),
        patch("gefapi.services.docker_service.Execution") as model,
        patch("gefapi.services.docker_service.db"),
        patch("gefapi.services.docker_service.rollbar.report_exc_info"),
    ):
        model.query.get.return_value = execution
        with pytest.raises(ValueError, match="owner is disabled"):
            docker_run.run(str(uuid4()))
    assert execution.status == "FAILED"


@pytest.mark.standalone
def test_openeo_worker_resolves_credentials_locally(caplog):
    from gefapi.services.openeo_service import openeo_run

    marker = "synthetic-openeo-marker"
    environment = {
        "OPENEO_BACKEND_URL": "https://openeo.example.test",
        "OPENEO_CREDENTIALS": marker,
    }
    execution_id = str(uuid4())
    execution = SimpleNamespace(results={})
    job = MagicMock(job_id="synthetic-job")
    with (
        patch(
            "gefapi.services.execution_service.load_execution_context",
            return_value=("script", environment, {"analysis": "unchanged"}),
        ) as load,
        patch("gefapi.services.openeo_service.Execution") as model,
        patch("gefapi.services.openeo_service.db"),
        patch("gefapi.services.openeo_service.ExecutionLog"),
        patch("gefapi.services.openeo_service._connect_openeo") as connect,
        patch(
            "gefapi.services.openeo_service._build_process_graph",
            return_value=job,
        ),
        caplog.at_level(logging.DEBUG),
    ):
        model.query.get.return_value = execution
        openeo_run.run(execution_id)
    load.assert_called_once_with(execution_id, "openeo")
    connect.assert_called_once_with("https://openeo.example.test", environment)
    job.start_job.assert_called_once()
    assert execution.status == "READY"
    assert marker not in caplog.text
