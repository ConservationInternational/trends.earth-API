"""
Tests for status monitoring endpoints
"""

from urllib.parse import urlencode

import pytest


class TestStatus:
    """Test status monitoring functionality"""

    def test_get_status_logs_admin_only(
        self, client, auth_headers_admin, auth_headers_user, sample_status_log
    ):
        """Test getting status logs (admin only)"""
        # Admin should be able to get status logs
        response = client.get("/api/v1/status", headers=auth_headers_admin)
        assert response.status_code == 200
        data = response.json
        assert "data" in data
        assert "page" in data
        assert "per_page" in data
        assert "total" in data

        # Regular user should be forbidden
        response = client.get("/api/v1/status", headers=auth_headers_user)
        assert response.status_code == 403

        # Unauthenticated should be unauthorized
        response = client.get("/api/v1/status")
        assert response.status_code == 401

    def test_get_status_logs_with_filters(
        self, client, auth_headers_admin, sample_status_log
    ):
        """Test getting status logs with date filters"""
        params = {"start_date": "2025-01-01", "end_date": "2025-12-31"}
        query_string = urlencode(params)

        response = client.get(
            f"/api/v1/status?{query_string}", headers=auth_headers_admin
        )
        assert response.status_code == 200

    def test_get_status_logs_with_sorting(
        self, client, auth_headers_admin, sample_status_log
    ):
        """Test getting status logs with sorting"""
        test_cases = [
            "timestamp",
            "-timestamp",
            "executions_active",
            "-executions_active",
            "cpu_usage_percent",
            "-cpu_usage_percent",
        ]

        for sort_param in test_cases:
            params = {"sort": sort_param}
            query_string = urlencode(params)

            response = client.get(
                f"/api/v1/status?{query_string}", headers=auth_headers_admin
            )
            assert response.status_code == 200, f"Sort parameter {sort_param} failed"

    def test_get_status_logs_pagination(self, client, auth_headers_admin):
        """Test status logs pagination"""
        # Test different page sizes
        for per_page in [10, 50, 100]:
            params = {"per_page": per_page, "page": 1}
            query_string = urlencode(params)

            response = client.get(
                f"/api/v1/status?{query_string}", headers=auth_headers_admin
            )
            assert response.status_code == 200
            data = response.json
            assert data["per_page"] == per_page

    def test_status_log_data_structure(
        self, client, auth_headers_admin, sample_status_log
    ):
        """Test status log data structure"""
        response = client.get("/api/v1/status", headers=auth_headers_admin)
        assert response.status_code == 200

        data = response.json["data"]
        if len(data) > 0:
            log_entry = data[0]
            required_fields = [
                "id",
                "timestamp",
                "executions_active",
                "executions_ready",
                "executions_running",
                "executions_finished",
                "users_count",
                "scripts_count",
                "memory_available_percent",
                "cpu_usage_percent",
            ]

            for field in required_fields:
                assert field in log_entry, f"Field {field} missing from status log"

            # Verify data types
            assert isinstance(log_entry["executions_active"], int)
            assert isinstance(log_entry["memory_available_percent"], (int, float))
            assert isinstance(log_entry["cpu_usage_percent"], (int, float))

    def test_status_logs_invalid_pagination(self, client, auth_headers_admin):
        """Test status logs with invalid pagination parameters"""
        # Test per_page too large (should be capped at 1000)
        response = client.get(
            "/api/v1/status?per_page=2000", headers=auth_headers_admin
        )
        assert response.status_code == 200
        data = response.json
        assert data["per_page"] <= 1000

    def test_status_logs_date_filtering(
        self, client, auth_headers_admin, sample_status_log
    ):
        """Test status logs date filtering functionality"""
        # Test with future date (should return no results)
        params = {"start_date": "2030-01-01"}
        query_string = urlencode(params)

        response = client.get(
            f"/api/v1/status?{query_string}", headers=auth_headers_admin
        )
        assert response.status_code == 200
        data = response.json
        assert data["total"] == 0

        # Test with past date range
        params = {"start_date": "2020-01-01", "end_date": "2030-01-01"}
        query_string = urlencode(params)

        response = client.get(
            f"/api/v1/status?{query_string}", headers=auth_headers_admin
        )
        assert response.status_code == 200
