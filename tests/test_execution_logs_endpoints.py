"""
Tests for execution log-related endpoints that were previously untested.

Covers:
- GET /execution/<id>/docker-logs
- GET /execution/<id>/batch-logs
- GET /execution/<id>/download-results
- POST /execution/<id>/log
"""

import json
from unittest.mock import patch
import uuid

import pytest

from gefapi import db


@pytest.fixture
def execution_id(app, sample_execution):
    """Get execution ID safely within app context."""
    with app.app_context():
        execution = db.session.merge(sample_execution)
        return str(execution.id)


@pytest.mark.usefixtures("client")
class TestGetExecutionDockerLogs:
    """Tests for GET /api/v1/execution/<id>/docker-logs"""

    def test_requires_auth(self, client):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/execution/{fake_id}/docker-logs")
        assert resp.status_code == 401

    def test_forbidden_for_regular_user(self, client, auth_headers_user, execution_id):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/docker-logs",
            headers=auth_headers_user,
        )
        assert resp.status_code == 403

    @patch(
        "gefapi.services.docker_service.DockerService.get_service_logs",
        return_value=[
            {"id": 0, "created_at": "2025-01-01T00:00:00Z", "text": "line 1"},
            {"id": 1, "created_at": "2025-01-01T00:00:01Z", "text": "line 2"},
        ],
    )
    def test_returns_logs_for_admin(
        self, mock_get_logs, client, auth_headers_admin, execution_id
    ):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/docker-logs",
            headers=auth_headers_admin,
        )
        assert resp.status_code == 200
        data = resp.json
        assert "data" in data
        assert len(data["data"]) == 2

    @patch(
        "gefapi.services.docker_service.DockerService.get_service_logs",
        return_value=None,
    )
    def test_returns_404_when_no_logs(
        self, mock_get_logs, client, auth_headers_admin, execution_id
    ):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/docker-logs",
            headers=auth_headers_admin,
        )
        assert resp.status_code == 404

    @patch(
        "gefapi.services.docker_service.DockerService.get_service_logs",
        side_effect=Exception("Docker error"),
    )
    def test_returns_500_on_exception(
        self, mock_get_logs, client, auth_headers_admin, execution_id
    ):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/docker-logs",
            headers=auth_headers_admin,
        )
        assert resp.status_code == 500


@pytest.mark.usefixtures("client")
class TestGetExecutionBatchLogs:
    """Tests for GET /api/v1/execution/<id>/batch-logs"""

    def test_requires_auth(self, client):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/execution/{fake_id}/batch-logs")
        assert resp.status_code == 401

    def test_forbidden_for_regular_user(self, client, auth_headers_user, execution_id):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/batch-logs",
            headers=auth_headers_user,
        )
        assert resp.status_code == 403

    @patch(
        "gefapi.services.batch_service.get_batch_logs",
        return_value=[
            {
                "id": 0,
                "created_at": "2025-01-01T00:00:00Z",
                "text": "batch line 1",
                "job_name": "extract",
            },
        ],
    )
    def test_returns_logs_for_admin(
        self, mock_get_batch, client, auth_headers_admin, execution_id
    ):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/batch-logs",
            headers=auth_headers_admin,
        )
        assert resp.status_code == 200
        data = resp.json
        assert "data" in data
        assert len(data["data"]) == 1

    @patch(
        "gefapi.services.batch_service.get_batch_logs",
        return_value=None,
    )
    def test_returns_404_when_no_logs(
        self, mock_get_batch, client, auth_headers_admin, execution_id
    ):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/batch-logs",
            headers=auth_headers_admin,
        )
        assert resp.status_code == 404

    @patch(
        "gefapi.services.batch_service.get_batch_logs",
        side_effect=Exception("Batch error"),
    )
    def test_returns_500_on_exception(
        self, mock_get_batch, client, auth_headers_admin, execution_id
    ):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/batch-logs",
            headers=auth_headers_admin,
        )
        assert resp.status_code == 500


@pytest.mark.usefixtures("client")
class TestDownloadResults:
    """Tests for GET /api/v1/execution/<id>/download-results"""

    def test_requires_auth(self, client):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/execution/{fake_id}/download-results")
        assert resp.status_code == 401

    def test_owner_can_download(self, client, auth_headers_user, execution_id):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/download-results",
            headers=auth_headers_user,
        )
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/plain")
        assert "attachment" in resp.headers.get("Content-Disposition", "")
        assert "results.json" in resp.headers.get("Content-Disposition", "")

    def test_admin_can_download(self, client, auth_headers_admin, execution_id):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/download-results",
            headers=auth_headers_admin,
        )
        assert resp.status_code == 200

    def test_nonexistent_execution_returns_404(self, client, auth_headers_user):
        fake_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/v1/execution/{fake_id}/download-results",
            headers=auth_headers_user,
        )
        assert resp.status_code == 404

    def test_download_returns_json_content(
        self, client, auth_headers_user, sample_execution, app
    ):
        # Set results on execution
        with app.app_context():
            execution = db.session.merge(sample_execution)
            execution.results = {"output": "test_value", "metrics": [1, 2, 3]}
            db.session.commit()
            exec_id = str(execution.id)

        resp = client.get(
            f"/api/v1/execution/{exec_id}/download-results",
            headers=auth_headers_user,
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["output"] == "test_value"
        assert data["metrics"] == [1, 2, 3]


@pytest.mark.usefixtures("client")
class TestCreateExecutionLog:
    """Tests for POST /api/v1/execution/<id>/log"""

    def test_requires_auth(self, client):
        fake_id = str(uuid.uuid4())
        resp = client.post(
            f"/api/v1/execution/{fake_id}/log",
            json={"text": "test", "level": "INFO"},
        )
        assert resp.status_code == 401

    def test_forbidden_for_regular_user(self, client, auth_headers_user, execution_id):
        resp = client.post(
            f"/api/v1/execution/{execution_id}/log",
            json={"text": "test log", "level": "INFO"},
            headers=auth_headers_user,
        )
        assert resp.status_code == 403

    def test_admin_can_create_log(self, client, auth_headers_admin, execution_id):
        resp = client.post(
            f"/api/v1/execution/{execution_id}/log",
            json={"text": "Admin created log entry", "level": "INFO"},
            headers=auth_headers_admin,
        )
        assert resp.status_code == 200
        data = resp.json
        assert "data" in data

    def test_missing_text_returns_400(self, client, auth_headers_admin, execution_id):
        resp = client.post(
            f"/api/v1/execution/{execution_id}/log",
            json={"level": "INFO"},
            headers=auth_headers_admin,
        )
        assert resp.status_code == 400

    def test_missing_level_returns_400(self, client, auth_headers_admin, execution_id):
        resp = client.post(
            f"/api/v1/execution/{execution_id}/log",
            json={"text": "test"},
            headers=auth_headers_admin,
        )
        assert resp.status_code == 400

    def test_invalid_level_returns_400(self, client, auth_headers_admin, execution_id):
        resp = client.post(
            f"/api/v1/execution/{execution_id}/log",
            json={"text": "test", "level": "INVALID"},
            headers=auth_headers_admin,
        )
        assert resp.status_code == 400

    def test_nonexistent_execution_returns_404(self, client, auth_headers_admin):
        fake_id = str(uuid.uuid4())
        resp = client.post(
            f"/api/v1/execution/{fake_id}/log",
            json={"text": "test log", "level": "INFO"},
            headers=auth_headers_admin,
        )
        assert resp.status_code == 404
