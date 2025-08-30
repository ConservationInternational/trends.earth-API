"""
Tests for status endpoint functionality.

Tests the /api/v1/status endpoint and StatusService.
"""

import pytest

from gefapi import db
from gefapi.models import StatusLog
from gefapi.services.status_service import StatusService


@pytest.mark.usefixtures("client", "auth_headers_admin")
class TestStatusEndpoint:
    """Test status endpoint functionality"""

    def test_status_endpoint_requires_auth(self, client):
        """Test that status endpoint requires authentication"""
        response = client.get("/api/v1/status")
        assert response.status_code == 401

    def test_status_endpoint_requires_admin(self, client, auth_headers_user):
        """Test that status endpoint requires admin privileges"""
        response = client.get("/api/v1/status", headers=auth_headers_user)
        assert response.status_code == 403

    def test_status_endpoint_returns_data(self, client, auth_headers_admin):
        """Test that status endpoint returns proper data structure"""
        # Create some test status log entries
        with client.application.app_context():
            for i in range(3):
                status_log = StatusLog(
                    executions_active=i + 1,
                    executions_ready=i,
                    executions_running=i + 1,
                    executions_finished=i * 2,
                    executions_failed=i,
                    executions_cancelled=i,
                )
                db.session.add(status_log)
            db.session.commit()

        response = client.get("/api/v1/status", headers=auth_headers_admin)

        assert response.status_code == 200
        data = response.json

        # Verify response structure
        assert "data" in data
        assert "page" in data
        assert "per_page" in data
        assert "total" in data

        # Verify we have status log entries
        assert len(data["data"]) > 0

        # Verify each entry has expected fields
        for entry in data["data"]:
            assert "id" in entry
            assert "timestamp" in entry
            assert "executions_active" in entry
            assert "executions_ready" in entry
            assert "executions_running" in entry
            assert "executions_finished" in entry
            assert "executions_failed" in entry
            assert "executions_cancelled" in entry

    def test_status_endpoint_pagination(self, client, auth_headers_admin):
        """Test status endpoint pagination"""
        # Create test data
        with client.application.app_context():
            for i in range(5):
                status_log = StatusLog(
                    executions_active=i,
                    executions_ready=i,
                    executions_running=i,
                    executions_finished=i,
                    executions_failed=i,
                    executions_cancelled=i,
                )
                db.session.add(status_log)
            db.session.commit()

        # Test pagination parameters
        response = client.get(
            "/api/v1/status?page=1&per_page=2", headers=auth_headers_admin
        )

        assert response.status_code == 200
        data = response.json

        assert data["page"] == 1
        assert data["per_page"] == 2
        assert len(data["data"]) <= 2

    def test_status_endpoint_sorting(self, client, auth_headers_admin):
        """Test status endpoint sorting"""
        response = client.get(
            "/api/v1/status?sort=-timestamp", headers=auth_headers_admin
        )

        assert response.status_code == 200
        data = response.json

        # Verify sorting (newest first by default anyway)
        if len(data["data"]) > 1:
            timestamps = [entry["timestamp"] for entry in data["data"]]
            assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.usefixtures("app")
class TestStatusService:
    """Test StatusService functionality"""

    def test_get_status_logs_basic(self, app):
        """Test basic status log retrieval"""
        with app.app_context():
            # Create test data
            status_log = StatusLog(
                executions_active=5,
                executions_ready=2,
                executions_running=3,
                executions_finished=10,
                executions_failed=1,
                executions_cancelled=2,
            )
            db.session.add(status_log)
            db.session.commit()

            # Test service method
            logs, total = StatusService.get_status_logs()

            assert total > 0
            assert len(logs) > 0
            assert logs[0].executions_cancelled == 2

    def test_get_status_logs_pagination(self, app):
        """Test status log pagination"""
        with app.app_context():
            # Create test data
            for i in range(5):
                status_log = StatusLog(
                    executions_active=i,
                    executions_ready=i,
                    executions_running=i,
                    executions_finished=i,
                    executions_failed=i,
                    executions_cancelled=i,
                )
                db.session.add(status_log)
            db.session.commit()

            # Test pagination
            logs, total = StatusService.get_status_logs(page=1, per_page=2)

            assert total >= 5
            assert len(logs) <= 2

    def test_get_status_logs_sorting(self, app):
        """Test status log sorting"""
        with app.app_context():
            # Test with sorting parameter
            logs, total = StatusService.get_status_logs(sort="-timestamp")

            # Should not error and return results ordered by timestamp desc
            assert isinstance(logs, list)
            assert isinstance(total, int)
