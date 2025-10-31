"""
Tests for status endpoint functionality.

Tests the /api/v1/status endpoint and StatusService.
"""

from datetime import UTC, datetime, timedelta

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
                    executions_pending=i + 1,
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
            assert "executions_pending" in entry
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
                    executions_pending=i,
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

    def test_status_endpoint_aggregated_weekly(self, client, auth_headers_admin):
        """Ensure aggregated weekly status data is returned for last-year period."""

        with client.application.app_context():
            db.session.query(StatusLog).delete()
            base = datetime.now(UTC) - timedelta(days=28)

            for week_offset, pending_count in enumerate([1, 3]):
                status_log = StatusLog(
                    executions_pending=pending_count,
                    executions_ready=pending_count,
                    executions_running=pending_count,
                    executions_finished=pending_count * 2,
                    executions_failed=0,
                    executions_cancelled=0,
                )
                status_log.timestamp = base + timedelta(days=7 * week_offset)
                db.session.add(status_log)

            db.session.commit()

        response = client.get(
            "/api/v1/status?aggregate=true&period=last_year&group_by=week",
            headers=auth_headers_admin,
        )

        assert response.status_code == 200
        payload = response.json

        assert payload["total"] == 2
        first_entry = payload["data"][0]
        assert "executions_active" in first_entry
        assert first_entry["executions_pending"] == 1
        assert first_entry["executions_ready"] == 1
        assert first_entry["executions_running"] == 1
        assert first_entry["executions_active"] == 3
        assert first_entry.get("cumulative_finished") == 2
        second_entry = payload["data"][1]
        assert second_entry.get("cumulative_finished") == 8


@pytest.mark.usefixtures("app")
class TestStatusService:
    """Test StatusService functionality"""

    def test_get_status_logs_basic(self, app):
        """Test basic status log retrieval"""
        with app.app_context():
            # Create test data
            status_log = StatusLog(
                executions_pending=5,
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
                    executions_pending=i,
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

    def test_get_status_logs_grouped_week(self, app):
        """Test grouping status logs by week produces aggregated counts."""
        with app.app_context():
            db.session.query(StatusLog).delete()
            base = datetime.now(UTC) - timedelta(days=21)

            for week_offset, pending_count in enumerate([2, 4]):
                log_entry = StatusLog(
                    executions_pending=pending_count,
                    executions_ready=pending_count,
                    executions_running=pending_count,
                    executions_finished=pending_count,
                    executions_failed=0,
                    executions_cancelled=0,
                )
                log_entry.timestamp = base + timedelta(days=7 * week_offset)
                db.session.add(log_entry)

            db.session.commit()

            grouped = StatusService.get_status_logs_grouped(
                group_by="week",
                start_date=base,
                end_date=base + timedelta(days=21),
                sort="timestamp",
            )

            assert len(grouped) == 2
            assert grouped[0]["executions_pending"] == 2
            assert grouped[0]["executions_active"] == 6

    def test_get_status_logs_grouped_with_cumulative(self, app):
        """Ensure cumulative totals are always returned for grouped results."""
        with app.app_context():
            db.session.query(StatusLog).delete()
            base = datetime.now(UTC) - timedelta(days=14)

            for offset, finished_count in enumerate([2, 5]):
                log_entry = StatusLog(
                    executions_pending=0,
                    executions_ready=0,
                    executions_running=0,
                    executions_finished=finished_count,
                    executions_failed=offset,
                    executions_cancelled=0,
                )
                log_entry.timestamp = base + timedelta(days=7 * offset)
                db.session.add(log_entry)

            db.session.commit()

            grouped = StatusService.get_status_logs_grouped(
                group_by="week",
                start_date=base,
                end_date=base + timedelta(days=14),
                sort="timestamp",
            )

            assert grouped[0]["cumulative_finished"] == 2
            assert grouped[1]["cumulative_finished"] == 7
            assert grouped[0]["cumulative_failed"] == 0
            assert grouped[0]["cumulative_cancelled"] == 0
            assert grouped[1]["cumulative_failed"] == 1

    def test_get_status_logs_grouped_uses_totals_for_completed_states(self, app):
        """Finished, failed, and cancelled counts should return totals for the period."""
        with app.app_context():
            db.session.query(StatusLog).delete()
            bucket = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

            first_entry = StatusLog(
                executions_pending=1,
                executions_ready=1,
                executions_running=1,
                executions_finished=5,
                executions_failed=2,
                executions_cancelled=1,
            )
            first_entry.timestamp = bucket

            second_entry = StatusLog(
                executions_pending=1,
                executions_ready=1,
                executions_running=1,
                executions_finished=9,
                executions_failed=4,
                executions_cancelled=3,
            )
            second_entry.timestamp = bucket + timedelta(hours=2)

            db.session.add_all([first_entry, second_entry])
            db.session.commit()

            grouped = StatusService.get_status_logs_grouped(
                group_by="day",
                start_date=bucket,
                end_date=bucket + timedelta(days=1),
            )

            # The grouped values should surface the total counts observed within the bucket.
            assert grouped[0]["executions_finished"] == 14
            assert grouped[0]["executions_failed"] == 6
            assert grouped[0]["executions_cancelled"] == 4

            # Cumulative totals should be populated automatically without additional parameters.
            assert grouped[0]["cumulative_finished"] == 14
            assert grouped[0]["cumulative_failed"] == 6
            assert grouped[0]["cumulative_cancelled"] == 4
