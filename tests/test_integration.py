"""
Integration tests for the API - test full workflows
"""

from unittest.mock import patch

import pytest


@pytest.mark.integration
class TestAPIIntegration:
    """Test complete API workflows"""

    def test_full_user_workflow(
        self, client, auth_headers_admin, sample_user_data, sample_script
    ):
        """Test complete user workflow: create user, login, create script,
        run execution"""

        # Step 1: Admin creates a new user
        response = client.post(
            "/api/v1/user",
            json=sample_user_data,
            headers=auth_headers_admin,
        )
        assert response.status_code == 200
        # user_id = response.json["data"]["id"]

        # Step 2: User logs in
        login_response = client.post(
            "/auth", json={"email": sample_user_data["email"], "password": "password"}
        )
        assert login_response.status_code == 200
        user_token = login_response.json["access_token"]
        user_headers = {"Authorization": f"Bearer {user_token}"}

        # Step 3: User views their profile
        profile_response = client.get("/api/v1/user/me", headers=user_headers)
        assert profile_response.status_code == 200

        # Step 4: User lists available scripts
        scripts_response = client.get("/api/v1/script", headers=user_headers)
        assert scripts_response.status_code == 200

        # Step 5: User runs a script
        with patch("gefapi.services.docker_service.docker_run") as mock_docker:
            mock_docker.delay.return_value = None
            execution_response = client.post(
                f"/api/v1/script/{sample_script.id}/run",
                json={"params": {}},
                headers=user_headers,
            )
            assert execution_response.status_code == 200

    def test_admin_management_workflow(
        self, client, auth_headers_admin, sample_user_data, sample_script_data
    ):
        """Test admin management workflow"""

        # Admin creates a script
        with patch("gefapi.s3.push_script_to_s3") as mock_s3:
            mock_s3.return_value = True
            script_response = client.post(
                "/api/v1/script",
                json=sample_script_data,
                headers=auth_headers_admin,
            )
            assert script_response.status_code == 200
            script_id = script_response.json["data"]["id"]

        # Admin creates a user
        user_response = client.post(
            "/api/v1/user",
            json=sample_user_data,
            headers=auth_headers_admin,
        )
        assert user_response.status_code == 200

        # Admin views all executions
        executions_response = client.get(
            "/api/v1/execution", headers=auth_headers_admin
        )
        assert executions_response.status_code == 200

        # Admin can delete the script
        delete_response = client.delete(
            f"/api/v1/script/{script_id}", headers=auth_headers_admin
        )
        assert delete_response.status_code == 200

    def test_error_handling_workflow(self, client, auth_headers_user):
        """Test error handling in workflows"""

        # Try to access non-existent script
        response = client.get(
            "/api/v1/script/00000000-0000-0000-0000-000000000000",
            headers=auth_headers_user,
        )
        assert response.status_code == 404

        # Try to run non-existent script
        response = client.post(
            "/api/v1/script/00000000-0000-0000-0000-000000000000/run",
            json={"params": {}},
            headers=auth_headers_user,
        )
        assert response.status_code == 404

        # Try to access execution without permission
        response = client.get("/api/v1/execution", headers=auth_headers_user)
        # Users should not see all executions, only their own
        assert response.status_code in [200, 403]

    def test_data_consistency_workflow(
        self, client, auth_headers_user, auth_headers_admin, sample_script
    ):
        """Test data consistency across operations"""

        # User runs execution
        with patch("gefapi.services.docker_service.docker_run") as mock_docker:
            mock_docker.delay.return_value = None
            response = client.post(
                f"/api/v1/script/{sample_script.id}/run",
                json={"params": {}},
                headers=auth_headers_user,
            )
            assert response.status_code == 200
            execution_id = response.json["data"]["id"]

        # User checks their executions
        user_executions = client.get(
            "/api/v1/execution/user", headers=auth_headers_user
        )
        assert user_executions.status_code == 200
        # all_executions = user_executions.json["data"]

        # Admin checks all executions
        admin_executions = client.get("/api/v1/execution", headers=auth_headers_admin)
        assert admin_executions.status_code == 200
        # admin_all = admin_executions.json["data"]

        # Check specific execution details
        execution_detail = client.get(
            f"/api/v1/execution/{execution_id}", headers=auth_headers_user
        )
        assert execution_detail.status_code == 200

    def test_pagination_and_sorting_workflow(
        self, client, auth_headers_admin, sample_script
    ):
        """Test pagination and sorting features"""

        # Create multiple executions
        execution_ids = []
        with patch("gefapi.services.docker_service.docker_run") as mock_docker:
            mock_docker.delay.return_value = None
            for i in range(5):
                response = client.post(
                    f"/api/v1/script/{sample_script.id}/run",
                    json={"params": {"iteration": i}},
                    headers=auth_headers_admin,
                )
                if response.status_code == 200:
                    execution_ids.append(response.json["data"]["id"])

        # Test pagination
        paginated_response = client.get(
            "/api/v1/execution?page[size]=2&page[number]=1", headers=auth_headers_admin
        )
        assert paginated_response.status_code == 200

        # Test sorting
        sorted_response = client.get(
            "/api/v1/execution?sort=createdAt", headers=auth_headers_admin
        )
        assert sorted_response.status_code == 200
        # sorted_executions = sorted_response.json["data"]

        # Verify sorting worked (if we have data)
        if sorted_response.json.get("data"):
            executions = sorted_response.json["data"]
            if len(executions) > 1:
                # Check if timestamps are in order
                timestamps = [exec_data.get("createdAt") for exec_data in executions]
                # Should be sorted chronologically
                assert all(
                    timestamps[i] <= timestamps[i + 1]
                    for i in range(len(timestamps) - 1)
                )
