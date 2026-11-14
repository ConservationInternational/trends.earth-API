import datetime
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
import pytest

from gefapi import db
from gefapi.errors import InvalidExecutionParameters
from gefapi.models import Execution, Script
from gefapi.services import batch_service
from gefapi.tasks import batch_monitoring
from gefapi.utils.batch_policy import (
    validate_batch_execution_params,
    validate_batch_policy,
)


@pytest.mark.standalone
def test_nextflow_default_must_be_publisher_approved():
    with pytest.raises(InvalidExecutionParameters):
        validate_batch_policy({"default_workflow_backend": "nextflow"})
    validate_batch_policy(
        {
            "allowed_workflow_backends": ["nextflow"],
            "default_workflow_backend": "nextflow",
        }
    )


@pytest.mark.standalone
@pytest.mark.parametrize(
    "params",
    [
        {"workflow_backend": "batch"},
        {"pipeline": [{"name": "first-stage", "command": ["stage"]}]},
        {"batch": {}},
        {"batch": {"job_queue": "unapproved"}},
        {"batch": {"command": ["custom-command"]}},
    ],
)
def test_nextflow_default_rejects_legacy_dispatch_and_invalid_resources(params):
    script = SimpleNamespace(
        batch_execution_config={
            "allowed_workflow_backends": ["nextflow"],
            "default_workflow_backend": "nextflow",
            "allowed_job_queues": ["approved-workers"],
        }
    )
    with pytest.raises(InvalidExecutionParameters):
        validate_batch_execution_params(params, script)


def test_nextflow_publisher_default_is_persisted_before_dispatch(
    app,
    batch_script,
    regular_user,
    db_session,
    monkeypatch,
):
    from gefapi.config import SETTINGS
    from gefapi.services.execution_service import ExecutionService

    monkeypatch.setitem(SETTINGS, "EXECUTION_QUEUE", {"ENABLED": False})
    script = db_session.merge(batch_script)
    user = db_session.merge(regular_user)
    script.batch_execution_config = {
        "allowed_workflow_backends": ["nextflow"],
        "default_workflow_backend": "nextflow",
    }
    db_session.commit()
    original = {"task_name": "default-workflow"}
    with patch("gefapi.services.execution_service._dispatch_execution") as dispatch:
        execution = ExecutionService.create_execution(
            script.id,
            original,
            user,
        )
    assert execution.params == {**original, "workflow_backend": "nextflow"}
    assert original == {"task_name": "default-workflow"}
    dispatch.assert_called_once_with(execution.id, compute_type="batch")


@pytest.mark.standalone
def test_nextflow_backend_requires_publisher_policy():
    script = SimpleNamespace(
        batch_execution_config={"allowed_workflow_backends": ["nextflow"]},
        batch_image=None,
        batch_job_queue=None,
        batch_job_definition=None,
    )
    validate_batch_execution_params({"workflow_backend": "nextflow"}, script)

    script.batch_execution_config = {}
    with pytest.raises(InvalidExecutionParameters):
        validate_batch_execution_params({"workflow_backend": "nextflow"}, script)


@pytest.mark.standalone
@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN", "USER", None])
def test_script_exposes_effective_nextflow_driver_only_to_admins(monkeypatch, role):
    default_arn = "arn:aws:batch:us-east-1:123456789012:job-definition/driver:4"
    monkeypatch.delenv("NEXTFLOW_DRIVER_JOB_DEFINITION", raising=False)
    monkeypatch.delenv("NEXTFLOW_DRIVER_JOB_QUEUE", raising=False)
    monkeypatch.setitem(
        batch_service.SETTINGS, "NEXTFLOW_DRIVER_JOB_DEFINITION", default_arn
    )
    monkeypatch.setitem(batch_service.SETTINGS, "NEXTFLOW_DRIVER_JOB_QUEUE", "default")
    script = Script("Nextflow", "nextflow", "owner", compute_type="batch")
    script.created_at = script.updated_at = datetime.datetime.now(datetime.UTC)
    user = SimpleNamespace(role=role) if role else None
    include = ["environment", "nextflow_driver"]

    data = script.serialize(include=include, user=user)
    if role not in {"ADMIN", "SUPERADMIN"}:
        assert "nextflow_driver" not in data
        return
    assert data["batch_job_definition"] is None
    assert data["batch_job_queue"] is None
    assert data["nextflow_driver"] == {
        "job_definition": default_arn,
        "job_queue": "default",
    }
    monkeypatch.setenv("NEXTFLOW_DRIVER_JOB_QUEUE", "environment")
    assert (
        script.serialize(include=include, user=user)["nextflow_driver"]["job_queue"]
        == "environment"
    )
    script.batch_job_definition = default_arn.replace(":4", ":5")
    script.batch_job_queue = "override"
    assert script.serialize(include=include, user=user)["nextflow_driver"] == {
        "job_definition": script.batch_job_definition,
        "job_queue": "override",
    }
    assert "nextflow_driver" not in script.serialize(include=["environment"], user=user)


@pytest.mark.standalone
def test_nextflow_resume_uses_pinned_image_without_ecr_lookup(monkeypatch):
    image = "123456789012.dkr.ecr.us-east-1.amazonaws.com/analysis@sha256:" + "b" * 64
    monkeypatch.setenv("NEXTFLOW_ENABLED", "true")
    monkeypatch.setenv(
        "NEXTFLOW_DRIVER_JOB_DEFINITION",
        "arn:aws:batch:us-east-1:123456789012:job-definition/driver:4",
    )
    monkeypatch.setenv("NEXTFLOW_DRIVER_JOB_QUEUE", "trusted-ondemand")
    monkeypatch.setenv("NEXTFLOW_WORK_ROOT", "s3://work-root/work")
    monkeypatch.setenv("NEXTFLOW_STATE_ROOT", "s3://state-root/state")
    monkeypatch.setattr(batch_service, "PARAMS_S3_BUCKET", "params-bucket")

    config = batch_service._nextflow_config(
        SimpleNamespace(batch_image=None), image_override=image
    )

    assert config["image"] == image

    script = SimpleNamespace(
        batch_image=None,
        batch_job_definition="arn:aws:batch:us-east-1:123456789012:job-definition/other-workflow:7",
        batch_job_queue="other-driver-queue",
    )
    config = batch_service._nextflow_config(script, image_override=image)
    assert config["job_definition"] == script.batch_job_definition
    assert config["job_queue"] == script.batch_job_queue


@pytest.mark.standalone
def test_driver_submission_uses_only_trusted_driver_settings():
    config = {
        "job_definition": "arn:aws:batch:us-east-1:123456789012:job-definition/driver:4",
        "job_queue": "trusted-ondemand",
        "work_root": "s3://work-root/work",
        "state_root": "s3://state-root/state",
        "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/analysis@sha256:"
        + "a" * 64,
        "max_hours": "12",
    }
    batch = MagicMock()
    batch.submit_job.return_value = {"jobId": "driver-job"}
    script = SimpleNamespace(batch_image="ignored-user-image")
    previous_metadata = {
        "image": (
            "123456789012.dkr.ecr.us-east-1.amazonaws.com/analysis@sha256:" + "b" * 64
        ),
        "work_uri": "s3://original-work/execution-id",
        "state_uri": "s3://original-state/execution-id",
        "params_s3_bucket": "original-params",
        "params_s3_prefix": "original-prefix",
        "driver_job_definition": "arn:aws:batch:us-east-1:123456789012:job-definition/driver:3",
        "driver_job_queue": "original-driver-queue",
    }

    def config_for_resume(_script, image_override=None):
        return {**config, "image": image_override or config["image"]}

    with (
        patch.object(batch_service, "_nextflow_config", side_effect=config_for_resume),
        patch.object(batch_service, "_get_batch_client", return_value=batch),
    ):
        result = batch_service.submit_nextflow_driver(
            "execution-id",
            "s3://params/execution-id.json.gz",
            script,
            resume=True,
            previous_metadata=previous_metadata,
        )

    submission = batch.submit_job.call_args.kwargs
    environment = {
        item["name"]: item["value"]
        for item in submission["containerOverrides"]["environment"]
    }
    assert submission["jobDefinition"] == previous_metadata["driver_job_definition"]
    assert submission["jobQueue"] == previous_metadata["driver_job_queue"]
    assert "command" not in submission["containerOverrides"]
    assert submission["retryStrategy"] == {"attempts": 1}
    assert submission["timeout"] == {"attemptDurationSeconds": 44100}
    assert submission["tags"] == {"execution_id": "execution-id"}
    assert environment["NEXTFLOW_TASK_IMAGE"] == previous_metadata["image"]
    assert environment["NEXTFLOW_RESUME"] == "true"
    assert environment["NEXTFLOW_MAX_HOURS"] == "12"
    assert environment["NEXTFLOW_STATE_URI"] == previous_metadata["state_uri"]
    assert environment["NEXTFLOW_WORK_URI"] == previous_metadata["work_uri"]
    assert environment["PARAMS_S3_BUCKET"] == previous_metadata["params_s3_bucket"]
    assert environment["PARAMS_S3_PREFIX"] == previous_metadata["params_s3_prefix"]
    assert not any(name.startswith("AE_") for name in environment)
    assert result["batch_jobs"] == {"nextflow": "driver-job"}


def test_queued_dispatch_persists_publisher_default(
    app, regular_user, batch_script, db_session
):
    user = db_session.merge(regular_user)
    script = db_session.merge(batch_script)
    script.batch_execution_config = {
        "allowed_workflow_backends": ["nextflow"],
        "default_workflow_backend": "nextflow",
    }
    original = {"workflow_input": {"seed": 42}}
    execution = Execution(script_id=script.id, params=original, user_id=user.id)
    execution.status = "READY"
    db_session.add(execution)
    db_session.commit()
    with (
        patch.object(
            batch_service, "push_params_to_s3", return_value="s3://params/input.json.gz"
        ) as upload,
        patch.object(
            batch_service,
            "submit_nextflow_driver",
            return_value={
                "batch_jobs": {"nextflow": "driver-job"},
                "nextflow": {"driver_job_id": "driver-job"},
            },
        ) as submit,
    ):
        batch_service.batch_run.run(execution.id, script.slug, {}, original)
    db_session.refresh(execution)
    expected = {**original, "workflow_backend": "nextflow"}
    assert execution.params == expected
    upload.assert_called_once_with(expected, str(execution.id))
    assert submit.call_args.kwargs["resume"] is False
    assert execution.results["batch_jobs"] == {"nextflow": "driver-job"}


def test_resume_dispatch_reuses_original_params_object(
    app, regular_user, batch_script, db_session
):
    user = db_session.merge(regular_user)
    script = db_session.merge(batch_script)
    script.batch_execution_config = {"allowed_workflow_backends": ["nextflow"]}
    params = {"workflow_backend": "nextflow", "immutable_input": {"seed": 42}}
    params_uri = "s3://params-bucket/execution-params/execution-id.json.gz"
    execution = Execution(
        script_id=script.id,
        params=params,
        user_id=user.id,
    )
    execution.results = {
        "batch_jobs": {},
        "nextflow": {
            "resume_requested": True,
            "params_s3_uri": params_uri,
            "params_s3_bucket": "params-bucket",
            "params_s3_prefix": "execution-params",
        },
    }
    execution.status = "READY"
    db_session.add(execution)
    db_session.commit()

    with (
        patch.object(batch_service, "push_params_to_s3") as upload,
        patch.object(
            batch_service,
            "submit_nextflow_driver",
            return_value={
                "batch_jobs": {"nextflow": "new-driver"},
                "nextflow": {"driver_job_id": "new-driver"},
            },
        ) as submit,
    ):
        batch_service.batch_run.run(
            execution.id,
            script.slug,
            {},
            params,
        )

    upload.assert_not_called()
    assert submit.call_args.args[:2] == (str(execution.id), params_uri)
    assert submit.call_args.kwargs["resume"] is True
    db_session.refresh(execution)
    assert execution.params == params


@pytest.mark.standalone
def test_nextflow_cancellation_marks_state_and_terminates_children(monkeypatch):
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": BytesIO(b'["child-job"]')}
    batch = MagicMock()
    batch.describe_jobs.return_value = {
        "jobs": [
            {"jobId": "driver-job", "status": "RUNNING"},
            {"jobId": "child-job", "status": "RUNNING"},
        ]
    }
    execution = SimpleNamespace(
        results={"batch_jobs": {"nextflow": "driver-job"}},
        params={"workflow_backend": "nextflow"},
    )
    monkeypatch.setenv("NEXTFLOW_STATE_ROOT", "s3://state-bucket/workflows")

    with (
        patch.object(batch_service, "Execution") as execution_model,
        patch.object(batch_service, "_get_s3_client", return_value=s3),
        patch.object(batch_service, "_get_batch_client", return_value=batch),
    ):
        execution_model.query.get.return_value = execution
        result = batch_service.terminate_batch_jobs("execution-id")

    assert result["cancellation_marker_written"] is True
    s3.put_object.assert_called_once()
    assert s3.put_object.call_args.kwargs["Key"] == (
        "workflows/execution-id/cancelled.json"
    )
    terminated = {call.kwargs["jobId"] for call in batch.terminate_job.call_args_list}
    assert terminated == {"driver-job", "child-job"}


@pytest.mark.standalone
@pytest.mark.parametrize("configured_queues", [True, False])
def test_nextflow_failure_cleanup_discovers_children_by_tag(
    monkeypatch, configured_queues
):
    queues = ["workflow-one-workers", "workflow-two-workers"]
    if configured_queues:
        monkeypatch.setenv("NEXTFLOW_CHILD_JOB_QUEUES", ",".join(queues))
    else:
        monkeypatch.delenv("NEXTFLOW_CHILD_JOB_QUEUES", raising=False)
    s3 = MagicMock()
    s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}}, "GetObject"
    )
    batch = MagicMock()
    batch.get_paginator.return_value.paginate.return_value = [
        {"jobQueues": [{"jobQueueArn": queue}]} for queue in queues
    ]
    batch.list_jobs.side_effect = lambda **kwargs: (
        {"jobSummaryList": [{"jobId": "orphan-child"}, {"jobId": "unrelated-job"}]}
        if kwargs["jobStatus"] == "RUNNING"
        else {"jobSummaryList": []}
    )
    batch.describe_jobs.side_effect = [
        {
            "jobs": [
                {
                    "jobId": "orphan-child",
                    "status": "RUNNING",
                    "tags": {"execution_id": "execution-id"},
                },
                {
                    "jobId": "unrelated-job",
                    "status": "RUNNING",
                    "tags": {"execution_id": "another-execution"},
                },
            ]
        },
        {"jobs": [{"jobId": "orphan-child", "status": "RUNNING"}]},
        {"jobs": [{"jobId": "orphan-child", "status": "FAILED"}]},
    ]
    execution = SimpleNamespace(
        results={
            "batch_jobs": {"nextflow": "driver-job"},
            "nextflow": {"state_uri": "s3://state/execution-id"},
        },
        params={"workflow_backend": "nextflow"},
    )

    with (
        patch.object(batch_service, "Execution") as execution_model,
        patch.object(batch_service, "_get_s3_client", return_value=s3),
        patch.object(batch_service, "_get_batch_client", return_value=batch),
    ):
        execution_model.query.get.return_value = execution
        cleanup = batch_service.terminate_nextflow_children(
            "execution-id", state_uri="s3://state/execution-id"
        )

    assert cleanup["cleaned"] is True
    assert {call.kwargs["jobQueue"] for call in batch.list_jobs.call_args_list} == set(
        queues
    )
    assert cleanup["jobs_terminated"] == ["orphan-child"]
    batch.terminate_job.assert_called_once_with(
        jobId="orphan-child",
        reason="Nextflow driver failed; cleaning up children",
    )


@pytest.mark.standalone
def test_nextflow_monitor_requires_state_and_result_file():
    execution = SimpleNamespace(
        id="execution-id",
        params={"workflow_backend": "nextflow"},
        results={
            "batch_jobs": {"nextflow": "driver-job"},
            "nextflow": {"session_id": "session-123"},
        },
        status="RUNNING",
        end_date=None,
    )
    statuses = {"nextflow": {"status": "SUCCEEDED"}}
    with (
        patch.object(
            batch_monitoring,
            "_fetch_nextflow_status",
            return_value={"phase": "SUCCEEDED", "success": True},
        ),
        patch.object(batch_monitoring, "_fetch_results_from_s3", return_value=None),
        patch.object(batch_monitoring.db.session, "add"),
    ):
        outcome = batch_monitoring._process_nextflow_execution(
            execution, {"nextflow": "driver-job"}, statuses
        )

    assert outcome == "FAILED"
    assert execution.status == "FAILED"
    assert "without successful state and results" in execution.results["error"]


@pytest.mark.standalone
def test_nextflow_monitor_preserves_session_identity_on_success():
    execution = SimpleNamespace(
        id="execution-id",
        params={"workflow_backend": "nextflow"},
        results={
            "batch_jobs": {"nextflow": "driver-job"},
            "nextflow": {"session_id": "session-123"},
        },
        status="RUNNING",
        end_date=None,
    )
    statuses = {"nextflow": {"status": "SUCCEEDED"}}
    with (
        patch.object(
            batch_monitoring,
            "_fetch_nextflow_status",
            return_value={
                "phase": "succeeded",
                "success": True,
                "session_id": "session-123",
            },
        ),
        patch.object(
            batch_monitoring,
            "_fetch_results_from_s3",
            return_value={"result": "ok"},
        ),
        patch.object(batch_monitoring.db.session, "add"),
    ):
        outcome = batch_monitoring._process_nextflow_execution(
            execution, {"nextflow": "driver-job"}, statuses
        )

    assert outcome == "FINISHED"
    assert execution.status == "FINISHED"
    assert execution.progress == 100
    assert execution.results["nextflow"]["session_id"] == "session-123"
    assert execution.results["batch_jobs"] == {"nextflow": "driver-job"}


@pytest.mark.standalone
def test_nextflow_driver_failure_waits_for_stable_child_cleanup():
    execution = SimpleNamespace(
        id="execution-id",
        params={"workflow_backend": "nextflow"},
        results={"nextflow": {"state_uri": "s3://state/execution-id"}},
        status="RUNNING",
        end_date=None,
    )
    statuses = {"nextflow": {"status": "FAILED", "reason": "driver lost"}}
    with (
        patch.object(
            batch_monitoring,
            "_fetch_nextflow_status",
            return_value={"phase": "failed", "success": False, "error": "driver lost"},
        ),
        patch(
            "gefapi.services.batch_service.terminate_nextflow_children",
            return_value={"cleaned": True, "active_job_ids": [], "errors": []},
        ),
        patch.object(batch_monitoring.db.session, "add"),
    ):
        first_result = batch_monitoring._process_nextflow_execution(
            execution, {"nextflow": "driver-job"}, statuses
        )
        assert first_result is None
        assert execution.status == "RUNNING"

        cleanup_state = execution.results["nextflow"]["child_cleanup"]
        cleanup_state["last_clean_at"] = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=60)
        ).isoformat()
        second_result = batch_monitoring._process_nextflow_execution(
            execution, {"nextflow": "driver-job"}, statuses
        )

    assert second_result == "FAILED"
    assert execution.status == "FAILED"
    assert execution.results["nextflow"]["child_cleanup"]["clean_checks"] == 2


@pytest.mark.standalone
def test_nextflow_monitor_reads_state_from_original_execution_uri():
    s3 = MagicMock()
    s3.get_object.side_effect = [
        {"Body": BytesIO(b'{"phase":"running"}')},
        {"Body": BytesIO(b'{"session_id":"session-123"}')},
        {"Body": BytesIO(b'{"percent":20,"driver_job_id":"driver-job"}')},
    ]

    with patch.object(batch_monitoring, "_s3_client", return_value=s3):
        state = batch_monitoring._fetch_nextflow_status(
            "execution-id", "s3://state-bucket/original/execution-id"
        )

    assert state == {
        "phase": "running",
        "session_id": "session-123",
        "progress": {"percent": 20, "driver_job_id": "driver-job"},
    }
    assert s3.get_object.call_args_list[0].kwargs["Key"] == (
        "original/execution-id/status.json"
    )
    assert s3.get_object.call_args_list[1].kwargs["Key"] == (
        "original/execution-id/session.json"
    )
    assert s3.get_object.call_args_list[2].kwargs["Key"] == (
        "original/execution-id/progress.json"
    )


@pytest.mark.standalone
def test_nextflow_monitor_applies_valid_progress_only_for_current_driver(caplog):
    execution = SimpleNamespace(
        id="execution-id",
        params={"workflow_backend": "nextflow"},
        results={},
        status="RUNNING",
        progress=0,
    )
    jobs = {"nextflow": "current-driver"}
    statuses = {"nextflow": {"status": "RUNNING"}}
    states = [
        {"progress": {"driver_job_id": "current-driver", "percent": 40}},
        {"progress": {"driver_job_id": "old-driver", "percent": 90}},
        {"progress": {"driver_job_id": "current-driver", "percent": 20}},
        {"progress": {"driver_job_id": "current-driver", "percent": 100}},
    ]
    with (
        patch.object(batch_monitoring, "_fetch_nextflow_status", side_effect=states),
        patch.object(batch_monitoring.db.session, "add"),
    ):
        for _ in states:
            batch_monitoring._process_nextflow_execution(execution, jobs, statuses)
            assert execution.progress == 40
    assert "Invalid Nextflow progress" in caplog.text


def test_nextflow_progress_is_visible_and_not_duplicated(
    app, client, auth_headers_user, regular_user, batch_script, db_session
):
    user = db_session.merge(regular_user)
    script = db_session.merge(batch_script)
    execution = Execution(
        script_id=script.id,
        params={"workflow_backend": "nextflow"},
        user_id=user.id,
    )
    execution.status = "RUNNING"
    execution.results = {
        "batch_jobs": {"nextflow": "driver"},
        "nextflow": {"state_uri": "s3://state/original", "driver_job_id": "driver"},
    }
    db_session.add(execution)
    db_session.commit()
    s3 = MagicMock()
    batch = MagicMock()
    manifest = ["job-1"]
    jobs = [{"jobId": "job-1", "status": "RUNNING"}]
    s3.get_object.side_effect = lambda **_: {
        "Body": BytesIO(json.dumps(manifest).encode())
    }
    batch.describe_jobs.side_effect = lambda **_: {"jobs": jobs}
    statuses = {"nextflow": {"status": "RUNNING"}}

    with (
        patch.object(
            batch_monitoring,
            "_fetch_nextflow_status",
            return_value={"progress": {"driver_job_id": "driver", "percent": 20}},
        ),
        patch.object(batch_monitoring, "_s3_client", return_value=s3),
        patch.object(batch_monitoring, "_batch_client", return_value=batch),
    ):
        for _ in range(2):
            batch_monitoring._process_nextflow_execution(
                execution, {"nextflow": "driver"}, statuses
            )
            db_session.commit()
        response = client.get(
            f"/api/v1/execution/{execution.id}", headers=auth_headers_user
        )
        assert response.status_code == 200
        assert response.get_json()["data"]["progress"] == 20
        manifest.append("job-2")
        jobs[:] = [
            {"jobId": "job-1", "status": "SUCCEEDED"},
            {"jobId": "job-2", "status": "RUNNING"},
        ]
        batch_monitoring._process_nextflow_execution(
            execution, {"nextflow": "driver"}, statuses
        )
        db_session.commit()
        jobs[1]["status"] = "SUCCEEDED"
        with (
            patch.object(
                batch_monitoring,
                "_fetch_nextflow_status",
                return_value={"phase": "succeeded", "success": True},
            ),
            patch.object(
                batch_monitoring, "_fetch_results_from_s3", return_value={"ok": True}
            ),
        ):
            batch_monitoring._process_nextflow_execution(
                execution, {"nextflow": "driver"}, {"nextflow": {"status": "SUCCEEDED"}}
            )
            db_session.commit()

    response = client.get(
        f"/api/v1/execution/{execution.id}/log", headers=auth_headers_user
    )
    assert response.status_code == 200
    texts = [item["text"] for item in response.get_json()["data"]]
    assert texts == [
        "Nextflow: 1 AWS Batch task submitted (1 new); 1 running",
        "Nextflow: 2 AWS Batch tasks submitted (1 new); 1 running, 1 succeeded",
        "Nextflow: 2 AWS Batch tasks submitted; 2 succeeded",
        "Nextflow workflow completed successfully",
    ]
    assert s3.get_object.call_args.kwargs == {
        "Bucket": "state",
        "Key": "original/jobs.json",
    }
    response = client.get(
        f"/api/v1/execution/{execution.id}", headers=auth_headers_user
    )
    assert response.get_json()["data"]["progress"] == 100


@pytest.mark.standalone
def test_nextflow_progress_batches_jobs_and_reports_missing_descriptions():
    execution = SimpleNamespace(id="execution-id")
    metadata = {"state_uri": "s3://state/execution-id"}
    job_ids = [f"job-{number:03}" for number in range(101)]
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": BytesIO(json.dumps(job_ids).encode())}
    batch = MagicMock()
    batch.describe_jobs.side_effect = lambda jobs: {
        "jobs": [
            {"jobId": job, "status": "RUNNING"} for job in jobs if job != job_ids[-1]
        ]
    }
    with (
        patch.object(batch_monitoring, "_s3_client", return_value=s3),
        patch.object(batch_monitoring, "_batch_client", return_value=batch),
        patch.object(batch_monitoring.db.session, "add") as add,
    ):
        batch_monitoring._log_nextflow_progress(execution, metadata)
    assert [
        len(call.kwargs["jobs"]) for call in batch.describe_jobs.call_args_list
    ] == [
        100,
        1,
    ]
    assert metadata["logged_progress"]["statuses"] == {"RUNNING": 100, "UNKNOWN": 1}
    assert "1 unknown" in add.call_args.args[0].text


@pytest.mark.standalone
def test_nextflow_progress_errors_do_not_advance_cursor(caplog):
    execution = SimpleNamespace(id="execution-id")
    metadata = {
        "state_uri": "s3://state/execution-id",
        "logged_progress": {"submitted": 1, "statuses": {"RUNNING": 1}},
    }
    s3 = MagicMock()
    s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject"
    )
    with (
        patch.object(batch_monitoring, "_s3_client", return_value=s3),
        patch.object(batch_monitoring.db.session, "add") as add,
    ):
        batch_monitoring._log_nextflow_progress(execution, metadata)
    assert metadata["logged_progress"]["submitted"] == 1
    assert "Could not read Nextflow progress" in caplog.text
    add.assert_not_called()


def test_resume_endpoint_requeues_same_failed_execution(
    app, client, auth_headers_user, regular_user, batch_script, db_session
):
    regular_user = db_session.merge(regular_user)
    script = db_session.merge(batch_script)
    script.batch_execution_config = {"allowed_workflow_backends": ["nextflow"]}
    params = {"workflow_backend": "nextflow", "immutable_input": {"seed": 42}}
    execution = Execution(
        script_id=script.id,
        params=params,
        user_id=regular_user.id,
    )
    execution.status = "FAILED"
    execution.results = {
        "batch_jobs": {"nextflow": "previous-driver"},
        "nextflow": {
            "session_id": "existing-session",
            "phase": "failed",
            "child_cleanup": {"clean_checks": 2},
            "logged_progress": {"submitted": 2, "statuses": {"FAILED": 2}},
        },
    }
    db_session.add(execution)
    db_session.commit()
    original_params = dict(execution.params)

    with (
        patch(
            "gefapi.services.execution_service.terminate_nextflow_children",
            return_value={"cleaned": True},
        ),
        patch("gefapi.services.execution_service._dispatch_execution") as dispatch,
    ):
        response = client.post(
            f"/api/v1/execution/{execution.id}/resume",
            headers=auth_headers_user,
        )

    assert response.status_code == 202
    data = response.get_json()["data"]
    assert data["id"] == str(execution.id)
    assert data["status"] == "READY"
    dispatch.assert_called_once_with(execution.id, compute_type="batch")
    db.session.refresh(execution)
    assert execution.params == original_params
    assert execution.results["nextflow"]["session_id"] == "existing-session"
    assert execution.results["nextflow"]["resume_requested"] is True
    assert execution.results["nextflow"]["resume_count"] == 1
    assert execution.results["nextflow"]["logged_progress"]["submitted"] == 2
    assert execution.results["nextflow"]["driver_job_history"] == ["previous-driver"]
    assert execution.results["batch_jobs"] == {}


def test_resume_respects_active_execution_queue(
    app, client, auth_headers_user, regular_user, batch_script, db_session
):
    regular_user = db_session.merge(regular_user)
    script = db_session.merge(batch_script)
    script.batch_execution_config = {"allowed_workflow_backends": ["nextflow"]}
    execution = Execution(
        script_id=script.id,
        params={"workflow_backend": "nextflow"},
        user_id=regular_user.id,
    )
    execution.status = "FAILED"
    execution.results = {
        "batch_jobs": {"nextflow": "previous-driver"},
        "nextflow": {
            "session_id": "existing-session",
            "phase": "failed",
            "child_cleanup": {"clean_checks": 2},
        },
    }
    db_session.add(execution)
    db_session.commit()

    with (
        patch(
            "gefapi.services.execution_service._get_user_active_execution_count",
            return_value=3,
        ),
        patch(
            "gefapi.services.execution_service.terminate_nextflow_children",
            return_value={"cleaned": True},
        ),
        patch("gefapi.services.execution_service._dispatch_execution") as dispatch,
    ):
        response = client.post(
            f"/api/v1/execution/{execution.id}/resume",
            headers=auth_headers_user,
        )

    assert response.status_code == 202
    assert response.get_json()["data"]["status"] == "PENDING"
    assert not dispatch.called
    db.session.refresh(execution)
    assert execution.queued_at is not None
    assert execution.results["nextflow"]["session_id"] == "existing-session"
    assert execution.results["nextflow"]["resume_requested"] is True
    assert execution.results["nextflow"]["driver_job_history"] == ["previous-driver"]
    assert execution.results["batch_jobs"] == {}


def test_resume_refuses_when_child_cleanup_is_unconfirmed(
    app, client, auth_headers_user, regular_user, batch_script, db_session
):
    regular_user = db_session.merge(regular_user)
    script = db_session.merge(batch_script)
    script.batch_execution_config = {"allowed_workflow_backends": ["nextflow"]}
    execution = Execution(
        script_id=script.id,
        params={"workflow_backend": "nextflow"},
        user_id=regular_user.id,
    )
    execution.status = "FAILED"
    execution.results = {
        "batch_jobs": {"nextflow": "previous-driver"},
        "nextflow": {
            "session_id": "existing-session",
            "child_cleanup": {"clean_checks": 2},
        },
    }
    db_session.add(execution)
    db_session.commit()

    with (
        patch(
            "gefapi.services.execution_service.terminate_nextflow_children",
            return_value={
                "cleaned": False,
                "active_job_ids": ["orphan-child"],
                "jobs_terminated": ["orphan-child"],
                "errors": [],
            },
        ),
        patch("gefapi.services.execution_service._dispatch_execution") as dispatch,
    ):
        response = client.post(
            f"/api/v1/execution/{execution.id}/resume",
            headers=auth_headers_user,
        )

    assert response.status_code == 409
    dispatch.assert_not_called()
    db.session.refresh(execution)
    assert execution.status == "FAILED"
    assert execution.results["nextflow"]["child_cleanup"]["active_job_ids"] == [
        "orphan-child"
    ]
