"""Security boundaries and legacy compatibility for execution APIs."""

import base64
import datetime
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask_jwt_extended import create_access_token
import jwt
import pytest

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.errors import InvalidExecutionParameters
from gefapi.models import Execution
from gefapi.services.batch_service import batch_run
from gefapi.services.execution_service import ExecutionService
from gefapi.services.execution_token_service import (
    create_execution_token,
    verify_execution_token,
)
from gefapi.utils.batch_policy import validate_batch_execution_params


@pytest.fixture
def regular_user(regular_user):
    return db.session.merge(regular_user)


@pytest.fixture
def sample_script(sample_script):
    return db.session.merge(sample_script)


@pytest.fixture
def batch_script(batch_script):
    return db.session.merge(batch_script)


@pytest.fixture
def sample_execution(sample_execution):
    execution = db.session.merge(sample_execution)
    execution.status = "RUNNING"
    db.session.commit()
    return execution


@pytest.mark.parametrize(
    "params",
    [
        {"batch": {"image": "untrusted.invalid/image"}},
        {"batch": {"command": ["sh", "-c", "printf AUDIT_MARKER"]}},
        {"batch": {"job_definition": "untrusted-definition"}},
        {"batch": {"job_queue": "untrusted-queue"}},
        {"pipeline": [{"name": "step", "command": ["sh"]}]},
        {"pipeline": [{"name": "step", "job_definition": "untrusted"}]},
        {"pipeline": [{"name": "step", "job_queue": "untrusted"}]},
        {"pipeline": [{"name": "step", "image": "untrusted"}]},
        {"batch": "invalid"},
        {"pipeline": {}},
        {"pipeline": [{}]},
    ],
)
def test_unapproved_batch_settings_rejected(params, batch_script, regular_user):
    with patch("gefapi.services.execution_service._dispatch_execution") as dispatch:
        with pytest.raises(InvalidExecutionParameters):
            ExecutionService.create_execution(batch_script.id, params, regular_user)
        dispatch.assert_not_called()
    assert Execution.query.filter_by(script_id=batch_script.id).count() == 0


def test_batch_route_returns_explicit_error(
    client,
    batch_script,
    auth_headers_user,
):
    response = client.post(
        f"/api/v1/script/{batch_script.id}/run",
        headers=auth_headers_user,
        json={"batch": {"command": ["unapproved"]}},
    )
    assert response.status_code == 400
    assert "approved" in response.json["detail"]


def test_worker_rechecks_queued_batch_params(sample_execution, batch_script):
    sample_execution.script_id = batch_script.id
    db.session.commit()
    with (
        patch("gefapi.services.batch_service._ensure_job_definition") as register,
        patch("gefapi.services.batch_service.push_params_to_s3") as upload,
        patch("gefapi.services.batch_service.rollbar"),
        patch.object(
            batch_run, "retry", side_effect=InvalidExecutionParameters("retry")
        ),
        pytest.raises(InvalidExecutionParameters),
    ):
        batch_run.run(
            sample_execution.id,
            batch_script.slug,
            {},
            {"pipeline": [{"name": "untrusted", "command": ["untrusted"]}]},
        )
    register.assert_not_called()
    upload.assert_not_called()
    assert sample_execution.status == "FAILED"


@pytest.mark.parametrize("pipeline", [False, True])
@pytest.mark.parametrize("id_only", [False, True])
def test_worker_preserves_approved_batch_dispatch(
    pipeline,
    id_only,
    sample_execution,
    batch_script,
):
    sample_execution.script_id = batch_script.id
    batch_script.batch_image = "approved.example/analysis:version"
    batch_script.batch_job_queue = "approved-queue"
    batch_script.batch_execution_config = {"allowed_commands": [["extract"]]}
    db.session.commit()
    params = {
        "task_name": "legacy-analysis",
        "batch": {"memory_mib": 16384, "vcpus": 4, "tags": {"Project": "test"}},
    }
    if pipeline:
        params["pipeline"] = [{"name": "extract", "command": ["extract"]}]
    sample_execution.params = params
    db.session.commit()
    with (
        patch.object(
            ExecutionService,
            "_build_execution_environment",
            return_value={"API_USER": "legacy"},
        ),
        patch(
            "gefapi.services.batch_service._ensure_job_definition",
            return_value="approved-definition",
        ) as register,
        patch(
            "gefapi.services.batch_service.push_params_to_s3",
            return_value="s3://test/params",
        ),
        patch(
            "gefapi.services.batch_service.submit_single_job",
            return_value={"job_id": "job"},
        ) as single,
        patch(
            "gefapi.services.batch_service.submit_pipeline",
            return_value={"extract": "job"},
        ) as steps,
    ):
        if id_only:
            batch_run.run(sample_execution.id)
        else:
            batch_run.run(
                sample_execution.id,
                batch_script.slug,
                {"API_USER": "legacy"},
                params,
            )
    register.assert_called_once_with(
        "approved.example/analysis:version",
        definition_name=None,
    )
    selected, unused = (steps, single) if pipeline else (single, steps)
    selected.assert_called_once()
    unused.assert_not_called()
    assert selected.call_args.args[2] == {"API_USER": "legacy"}
    assert selected.call_args.kwargs["job_queue"] == "approved-queue"
    assert selected.call_args.kwargs["resource_overrides"] == [
        {"type": "VCPU", "value": "4"},
        {"type": "MEMORY", "value": "16384"},
    ]
    assert selected.call_args.kwargs["tags"] == {"Project": "test"}
    if pipeline:
        assert selected.call_args.args[3] == params["pipeline"]
    assert sample_execution.status == "RUNNING"


def test_batch_policy_requires_administrator(
    client,
    batch_script,
    auth_headers_user,
    auth_headers_admin,
):
    url = f"/api/v1/script/{batch_script.id}/config"
    policy = {"allowed_commands": [["extract"], ["match_chunk_{index}"]]}
    response = client.patch(
        url,
        headers=auth_headers_user,
        json={"batch_execution_config": policy},
    )
    assert response.status_code == 403
    response = client.patch(
        url,
        headers=auth_headers_admin,
        json={"batch_execution_config": policy},
    )
    assert response.status_code == 200
    db.session.refresh(batch_script)
    assert batch_script.batch_execution_config == policy


def test_batch_policy_migration_round_trip(app):
    from sqlalchemy import inspect, text

    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/6f3a90c2d8e1_add_batch_execution_policy.py"
    )
    spec = importlib.util.spec_from_file_location("batch_policy_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with db.engine.connect() as connection, connection.begin():
        connection.execute(text("CREATE SCHEMA security_migration_test"))
        connection.execute(text("SET LOCAL search_path TO security_migration_test"))
        connection.execute(text("CREATE TABLE script (id varchar PRIMARY KEY)"))
        connection.execute(text("INSERT INTO script (id) VALUES ('existing-script')"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
            column = next(
                item
                for item in inspect(connection).get_columns("script")
                if item["name"] == "batch_execution_config"
            )
            assert column["nullable"]
            assert (
                connection.execute(
                    text("SELECT batch_execution_config FROM script")
                ).scalar()
                is None
            )
            migration.downgrade()
            assert "batch_execution_config" not in {
                item["name"] for item in inspect(connection).get_columns("script")
            }
        connection.execute(text("DROP TABLE script"))
        connection.execute(text("DROP SCHEMA security_migration_test"))


def test_trusted_pipeline_and_resource_tuning_remain_supported(batch_script):
    batch_script.batch_job_queue = "approved-queue"
    batch_script.batch_job_definition = "approved-definition"
    batch_script.batch_execution_config = {
        "allowed_commands": [
            ["extract"],
            ["match"],
            ["match_chunk_{index}"],
            ["summarize"],
        ],
        "allowed_job_queues": ["approved-large-queue"],
    }
    params = {
        "task_name": "legacy-analysis",
        "batch": {
            "job_queue": "approved-large-queue",
            "job_definition": "approved-definition",
            "vcpus": 4,
            "memory_mib": 16384,
            "timeout_seconds": 7200,
            "tags": {"Project": "test"},
        },
        "pipeline": [
            {"name": "extract", "command": ["extract"]},
            {"name": "match", "command": ["match_chunk_12"], "array_size": 5},
            {"name": "summarize", "command": ["summarize"]},
        ],
    }
    validate_batch_execution_params(params, batch_script)
    assert params["pipeline"][1]["command"] == ["match_chunk_12"]
    params["pipeline"][1]["command"] = ["match_chunk_12;printf AUDIT_MARKER"]
    with pytest.raises(InvalidExecutionParameters):
        validate_batch_execution_params(params, batch_script)


def test_legacy_analysis_parameters_unchanged(
    sample_script,
    regular_user,
    monkeypatch,
):
    monkeypatch.setitem(SETTINGS, "EXECUTION_QUEUE", {"ENABLED": False})
    params = {"task_notes": "notes", "local_context": {}, "geojsons": ["marker"]}
    with patch("gefapi.services.execution_service._dispatch_execution") as dispatch:
        execution = ExecutionService.create_execution(
            sample_script.id,
            params,
            regular_user,
        )
    assert execution.params == params
    dispatch.assert_called_once_with(execution.id, compute_type="gee")
    environment = ExecutionService._build_execution_environment(
        regular_user, execution.id, script=sample_script
    )
    assert environment["API_USER"]
    assert environment["API_PASSWORD"]
    assert environment["API_EXECUTION_TOKEN"]


@pytest.fixture
def legacy_service_account_key():
    return {
        "type": "service_account",
        "project_id": "test-project",
        "private_key_id": "synthetic",
        "private_key": "-----BEGIN PRIVATE KEY-----\nsynthetic",
        "client_email": "test@test-project.iam.gserviceaccount.com",
        "client_id": "123",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://accounts.google.com/o/oauth2/token",
    }


def test_workload_credentials_are_sanitized(
    regular_user,
    legacy_service_account_key,
):
    regular_user.gee_credentials_type = "service_account"
    with (
        patch.object(regular_user, "has_gee_credentials", return_value=True),
        patch.object(
            regular_user,
            "get_gee_service_account",
            return_value=legacy_service_account_key,
        ),
    ):
        environment = ExecutionService._build_execution_environment(
            regular_user,
            str(uuid.uuid4()),
        )
    key = json.loads(base64.b64decode(environment["EE_SERVICE_ACCOUNT_JSON"]))
    assert key["token_uri"] == "https://oauth2.googleapis.com/token"
    assert key["universe_domain"] == "googleapis.com"
    assert legacy_service_account_key["token_uri"] != key["token_uri"]


def test_stored_unsafe_credentials_rejected_before_execution_creation(
    sample_script,
    regular_user,
    legacy_service_account_key,
):
    regular_user.gee_credentials_type = "service_account"
    legacy_service_account_key["token_uri"] = "http://127.0.0.1/token"
    with (
        patch.object(
            regular_user,
            "get_gee_service_account",
            return_value=legacy_service_account_key,
        ),
        patch("gefapi.services.execution_service._dispatch_execution") as dispatch,
        pytest.raises(InvalidExecutionParameters, match="replace unsupported"),
    ):
        ExecutionService.create_execution(sample_script.id, {}, regular_user)
    dispatch.assert_not_called()
    assert Execution.query.filter_by(script_id=sample_script.id).count() == 0


@pytest.mark.parametrize("endpoint", ["/api/v1/script", "/api/v1/script/{id}"])
def test_nested_executions_respect_owner(
    endpoint,
    client,
    sample_script,
    sample_execution,
    admin_user,
    auth_headers_user,
    auth_headers_admin,
):
    victim = Execution(
        script_id=sample_script.id,
        params={"private": "victim-location"},
        user_id=admin_user.id,
    )
    db.session.add(victim)
    db.session.commit()
    url = endpoint.format(id=sample_script.id) + "?include=executions"
    response = client.get(url, headers=auth_headers_user)
    assert response.status_code == 200
    scripts = response.json["data"]
    scripts = scripts if isinstance(scripts, list) else [scripts]
    ids = {e["id"] for script in scripts for e in script["executions"]}
    assert str(sample_execution.id) in ids
    assert str(victim.id) not in ids
    assert "victim-location" not in response.get_data(as_text=True)
    response = client.get(url, headers=auth_headers_admin)
    assert response.status_code == 200
    assert str(victim.id) in response.get_data(as_text=True)


@pytest.mark.parametrize("endpoint", ["/api/v1/script", "/api/v1/script/{id}"])
def test_script_read_token_cannot_include_executions(
    endpoint,
    client,
    sample_script,
    regular_user,
):
    token = create_access_token(
        identity=regular_user.id,
        additional_claims={
            "grant_type": "client_credentials",
            "scopes": "script:read",
            "auth_version": regular_user.auth_version,
        },
    )
    headers = {"Authorization": f"Bearer {token}"}
    url = endpoint.format(id=sample_script.id)
    assert client.get(url, headers=headers).status_code == 200
    assert (
        client.get(
            url + "?include=executions",
            headers=headers,
        ).status_code
        == 403
    )


def test_nested_serialization_without_identity_fails_closed(sample_script):
    assert sample_script.serialize(include=["executions"])["executions"] == []


def test_execution_token_is_not_a_user_token(
    client,
    sample_execution,
    regular_user,
):
    token = create_execution_token(sample_execution.id, regular_user)
    response = client.get(
        "/api/v1/user/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code in {401, 422}


def test_callback_progress_and_log(
    client,
    sample_execution,
    regular_user,
):
    token = create_execution_token(sample_execution.id, regular_user)
    headers = {"Authorization": f"Bearer {token}"}
    base = f"/api/v1/execution/{sample_execution.id}/callback"
    response = client.patch(base, json={"progress": 25}, headers=headers)
    assert response.status_code == 200
    assert response.json["data"]["progress"] == 25
    response = client.post(
        base + "/log",
        json={"text": "safe marker", "level": "INFO"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json["data"]["text"] == "safe marker"


@pytest.mark.parametrize("kind", ["cross-execution", "revoked", "inactive", "terminal"])
def test_callback_token_boundaries(
    kind,
    client,
    sample_execution,
    regular_user,
):
    token = create_execution_token(sample_execution.id, regular_user)
    target = str(sample_execution.id)
    if kind == "cross-execution":
        target = str(uuid.uuid4())
    elif kind == "revoked":
        regular_user.auth_version += 1
    elif kind == "inactive":
        regular_user.is_active = False
    else:
        sample_execution.status = "FINISHED"
    db.session.commit()
    response = client.patch(
        f"/api/v1/execution/{target}/callback",
        headers={"Authorization": f"Bearer {token}"},
        json={"progress": 1},
    )
    assert response.status_code == 401


def test_expired_callback_token_rejected(sample_execution, regular_user, monkeypatch):
    monkeypatch.setitem(SETTINGS, "EXECUTION_TOKEN_TTL_SECONDS", -1)
    token = create_execution_token(sample_execution.id, regular_user)
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_execution_token(token, str(sample_execution.id))


def test_user_token_cannot_use_callback(
    client,
    sample_execution,
    auth_headers_admin,
):
    response = client.patch(
        f"/api/v1/execution/{sample_execution.id}/callback",
        headers=auth_headers_admin,
        json={"progress": 1},
    )
    assert response.status_code == 401


def test_callback_cannot_change_ownership(client, sample_execution, regular_user):
    token = create_execution_token(sample_execution.id, regular_user)
    response = client.patch(
        f"/api/v1/execution/{sample_execution.id}/callback",
        headers={"Authorization": f"Bearer {token}"},
        json={"progress": 1, "user_id": str(uuid.uuid4())},
    )
    assert response.status_code == 400


def test_callback_completion_invalidates_token(
    client,
    sample_execution,
    regular_user,
):
    regular_user.email_notifications_enabled = False
    db.session.commit()
    token = create_execution_token(sample_execution.id, regular_user)
    url = f"/api/v1/execution/{sample_execution.id}/callback"
    headers = {"Authorization": f"Bearer {token}"}
    response = client.patch(
        url,
        headers=headers,
        json={"status": "FINISHED", "results": {"ok": True}},
    )
    assert response.status_code == 200
    assert client.patch(url, headers=headers, json={"progress": 0}).status_code == 401
    assert sample_execution.end_date <= datetime.datetime.now(datetime.UTC).replace(
        tzinfo=None,
    )
