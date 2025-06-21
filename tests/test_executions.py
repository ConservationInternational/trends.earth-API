"""
Tests for execution management endpoints
"""

from urllib.parse import urlencode

import pytest


class TestExecutions:
    """Test execution management functionality"""

    def test_run_script(self, client, auth_headers_user, sample_script):
        """Test running a script"""
        script_id = str(sample_script.id)
        params = {"test_param": "test_value"}

        response = client.post(
            f"/api/v1/script/{script_id}/run", json=params, headers=auth_headers_user
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["script_id"] == script_id
        assert data["status"] == "PENDING"

    def test_run_nonexistent_script(self, client, auth_headers_user):
        """Test running non-existent script"""
        fake_id = "12345678-1234-1234-1234-123456789012"
        response = client.post(
            f"/api/v1/script/{fake_id}/run", json={}, headers=auth_headers_user
        )
        assert response.status_code == 404

    def test_get_executions(self, client, auth_headers_user, sample_execution):
        """Test getting list of executions"""
        response = client.get("/api/v1/execution", headers=auth_headers_user)

        assert response.status_code == 200
        data = response.json
        assert "data" in data
        assert "page" in data
        assert "per_page" in data
        assert "total" in data
        assert len(data["data"]) >= 1

    def test_get_executions_with_filters(
        self, client, auth_headers_user, sample_execution
    ):
        """Test getting executions with filters"""
        params = {"status": "FINISHED", "page": 1, "per_page": 10}
        query_string = urlencode(params)

        response = client.get(
            f"/api/v1/execution?{query_string}", headers=auth_headers_user
        )

        assert response.status_code == 200
        data = response.json
        assert data["page"] == 1
        assert data["per_page"] == 10
        # All returned executions should have status FINISHED
        for execution in data["data"]:
            assert execution["status"] == "FINISHED"

    def test_get_executions_with_date_filters(
        self, client, auth_headers_user, sample_execution
    ):
        """Test getting executions with date filters"""
        params = {"start_date_gte": "2025-01-01", "end_date_lte": "2025-12-31"}
        query_string = urlencode(params)

        response = client.get(
            f"/api/v1/execution?{query_string}", headers=auth_headers_user
        )
        assert response.status_code == 200

    def test_get_executions_with_sorting(
        self, client, auth_headers_user, sample_execution
    ):
        """Test getting executions with sorting"""
        test_cases = [
            "status",
            "-status",
            "start_date",
            "-start_date",
            "end_date",
            "-end_date",
            "duration",
            "-duration",
            "script_name",
            "-script_name",
            "user_name",
            "-user_name",
        ]

        for sort_param in test_cases:
            params = {"sort": sort_param}
            query_string = urlencode(params)

            response = client.get(
                f"/api/v1/execution?{query_string}", headers=auth_headers_user
            )
            assert response.status_code == 200, f"Sort parameter {sort_param} failed"

    def test_get_executions_with_include_duration(
        self, client, auth_headers_user, sample_execution
    ):
        """Test getting executions with duration included"""
        params = {"include": "duration"}
        query_string = urlencode(params)

        response = client.get(
            f"/api/v1/execution?{query_string}", headers=auth_headers_user
        )

        assert response.status_code == 200
        data = response.json["data"]
        if len(data) > 0:
            assert "duration" in data[0]
            assert isinstance(data[0]["duration"], (int, float))

    def test_get_executions_with_include_user_script(
        self, client, auth_headers_user, sample_execution
    ):
        """Test getting executions with user and script info"""
        params = {"include": "user,script"}
        query_string = urlencode(params)

        response = client.get(
            f"/api/v1/execution?{query_string}", headers=auth_headers_user
        )

        assert response.status_code == 200
        data = response.json["data"]
        if len(data) > 0:
            assert "user" in data[0]
            assert "script" in data[0]

    def test_get_executions_with_exclude(
        self, client, auth_headers_user, sample_execution
    ):
        """Test getting executions with excluded fields"""
        params = {"exclude": "params,results"}
        query_string = urlencode(params)

        response = client.get(
            f"/api/v1/execution?{query_string}", headers=auth_headers_user
        )

        assert response.status_code == 200
        data = response.json["data"]
        if len(data) > 0:
            assert "params" not in data[0]
            assert "results" not in data[0]

    def test_get_execution_by_id(self, client, auth_headers_user, sample_execution):
        """Test getting specific execution"""
        execution_id = str(sample_execution.id)
        response = client.get(
            f"/api/v1/execution/{execution_id}", headers=auth_headers_user
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["id"] == execution_id
        assert data["status"] == "FINISHED"

    def test_get_nonexistent_execution(self, client, auth_headers_user):
        """Test getting non-existent execution"""
        fake_id = "12345678-1234-1234-1234-123456789012"
        response = client.get(f"/api/v1/execution/{fake_id}", headers=auth_headers_user)
        assert response.status_code == 404

    def test_update_execution_admin_only(
        self, client, auth_headers_admin, auth_headers_user, sample_execution
    ):
        """Test updating execution (admin only)"""
        execution_id = str(sample_execution.id)
        update_data = {"status": "FAILED", "progress": 100}

        # Regular user should be forbidden
        response = client.patch(
            f"/api/v1/execution/{execution_id}",
            json=update_data,
            headers=auth_headers_user,
        )
        assert response.status_code == 403

        # Admin should be able to update
        response = client.patch(
            f"/api/v1/execution/{execution_id}",
            json=update_data,
            headers=auth_headers_admin,
        )
        assert response.status_code == 200
        assert response.json["data"]["status"] == "FAILED"

    def test_get_execution_logs(self, client, sample_execution):
        """Test getting execution logs (no auth required for this endpoint)"""
        execution_id = str(sample_execution.id)
        response = client.get(f"/api/v1/execution/{execution_id}/log")

        assert response.status_code == 200
        assert "data" in response.json

    def test_get_execution_logs_with_params(self, client, sample_execution):
        """Test getting execution logs with parameters"""
        execution_id = str(sample_execution.id)
        response = client.get(
            f"/api/v1/execution/{execution_id}/log?start=2025-01-01&last-id=1"
        )

        assert response.status_code == 200

    def test_create_execution_log_admin_only(
        self, client, auth_headers_admin, auth_headers_user, sample_execution
    ):
        """Test creating execution log (admin only)"""
        execution_id = str(sample_execution.id)
        log_data = {"text": "Test log message", "level": "INFO"}

        # Regular user should be forbidden
        response = client.post(
            f"/api/v1/execution/{execution_id}/log",
            json=log_data,
            headers=auth_headers_user,
        )
        assert response.status_code == 403

        # Admin should be able to create log
        response = client.post(
            f"/api/v1/execution/{execution_id}/log",
            json=log_data,
            headers=auth_headers_admin,
        )
        assert response.status_code == 200
        assert response.json["data"]["text"] == "Test log message"

    def test_download_execution_results(self, client, sample_execution):
        """Test downloading execution results"""
        execution_id = str(sample_execution.id)
        response = client.get(f"/api/v1/execution/{execution_id}/download-results")

        assert response.status_code == 200
        assert response.headers["Content-Type"] == "text/plain; charset=utf-8"

    def test_get_executions_pagination(self, client, auth_headers_user):
        """Test execution pagination"""
        # Test different page sizes
        for per_page in [1, 5, 10, 20]:
            params = {"per_page": per_page, "page": 1}
            query_string = urlencode(params)

            response = client.get(
                f"/api/v1/execution?{query_string}", headers=auth_headers_user
            )
            assert response.status_code == 200
            data = response.json
            assert data["per_page"] == per_page
            assert len(data["data"]) <= per_page

    def test_get_executions_invalid_pagination(self, client, auth_headers_user):
        """Test execution pagination with invalid parameters"""
        # Test invalid page
        response = client.get("/api/v1/execution?page=0", headers=auth_headers_user)
        assert response.status_code == 200  # Should default to page 1

        # Test invalid per_page
        response = client.get("/api/v1/execution?per_page=0", headers=auth_headers_user)
        assert response.status_code == 200  # Should default to valid value

        # Test per_page too large
        response = client.get(
            "/api/v1/execution?per_page=1000", headers=auth_headers_user
        )
        assert response.status_code == 200
        data = response.json
        assert data["per_page"] <= 100  # Should be capped at maximum
