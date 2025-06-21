"""
Integration tests for complete workflows
"""

import pytest


class TestIntegration:
    """Test complete API workflows"""

    def test_complete_user_workflow(self, client):
        """Test complete user registration and management workflow"""
        # 1. Create a new user
        user_data = {
            "email": "workflow@test.com",
            "password": "password123",
            "name": "Workflow User",
            "country": "Test Country",
            "institution": "Test Institution",
        }

        response = client.post("/api/v1/user", json=user_data)
        assert response.status_code == 200
        user_id = response.json["data"]["id"]

        # 2. Login with the new user
        response = client.post(
            "/auth", json={"email": "workflow@test.com", "password": "password123"}
        )
        assert response.status_code == 200
        token = response.json["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 3. Get user profile
        response = client.get("/api/v1/user/me", headers=headers)
        assert response.status_code == 200
        assert response.json["data"]["email"] == "workflow@test.com"

        # 4. Update user profile
        update_data = {"name": "Updated Workflow User"}
        response = client.patch("/api/v1/user/me", json=update_data, headers=headers)
        assert response.status_code == 200
        assert response.json["data"]["name"] == "Updated Workflow User"

    def test_script_execution_workflow(self, client, auth_headers_user, sample_script):
        """Test complete script execution workflow"""
        script_id = str(sample_script.id)

        # 1. Get user's scripts
        response = client.get("/api/v1/script", headers=auth_headers_user)
        assert response.status_code == 200
        assert len(response.json["data"]) >= 1

        # 2. Get specific script details
        response = client.get(f"/api/v1/script/{script_id}", headers=auth_headers_user)
        assert response.status_code == 200

        # 3. Run the script
        params = {"test_param": "integration_test"}
        response = client.post(
            f"/api/v1/script/{script_id}/run", json=params, headers=auth_headers_user
        )
        assert response.status_code == 200
        execution_id = response.json["data"]["id"]

        # 4. Check execution status
        response = client.get(
            f"/api/v1/execution/{execution_id}", headers=auth_headers_user
        )
        assert response.status_code == 200
        assert response.json["data"]["status"] == "PENDING"

        # 5. Get execution logs
        response = client.get(f"/api/v1/execution/{execution_id}/log")
        assert response.status_code == 200

        # 6. Get all user's executions
        response = client.get("/api/v1/execution", headers=auth_headers_user)
        assert response.status_code == 200
        executions = response.json["data"]
        execution_ids = [ex["id"] for ex in executions]
        assert execution_id in execution_ids

    def test_admin_workflow(self, client, auth_headers_admin, regular_user):
        """Test admin-specific workflow"""
        user_id = str(regular_user.id)

        # 1. Get all users (admin only)
        response = client.get("/api/v1/user", headers=auth_headers_admin)
        assert response.status_code == 200
        users = response.json["data"]
        assert len(users) >= 1

        # 2. Get specific user details
        response = client.get(f"/api/v1/user/{user_id}", headers=auth_headers_admin)
        assert response.status_code == 200

        # 3. Update user as admin
        update_data = {"name": "Admin Updated User"}
        response = client.patch(
            f"/api/v1/user/{user_id}", json=update_data, headers=auth_headers_admin
        )
        assert response.status_code == 200

        # 4. Get system status (admin only)
        response = client.get("/api/v1/status", headers=auth_headers_admin)
        assert response.status_code == 200
        assert "data" in response.json

    def test_execution_filtering_workflow(
        self, client, auth_headers_user, sample_execution
    ):
        """Test execution filtering and sorting workflow"""
        # 1. Get all executions
        response = client.get("/api/v1/execution", headers=auth_headers_user)
        assert response.status_code == 200
        all_executions = response.json["data"]

        # 2. Filter by status
        response = client.get(
            "/api/v1/execution?status=FINISHED", headers=auth_headers_user
        )
        assert response.status_code == 200
        finished_executions = response.json["data"]
        for execution in finished_executions:
            assert execution["status"] == "FINISHED"

        # 3. Sort by start date
        response = client.get(
            "/api/v1/execution?sort=-start_date", headers=auth_headers_user
        )
        assert response.status_code == 200
        sorted_executions = response.json["data"]

        # 4. Include duration information
        response = client.get(
            "/api/v1/execution?include=duration", headers=auth_headers_user
        )
        assert response.status_code == 200
        executions_with_duration = response.json["data"]
        if len(executions_with_duration) > 0:
            assert "duration" in executions_with_duration[0]

        # 5. Paginate results
        response = client.get(
            "/api/v1/execution?per_page=1&page=1", headers=auth_headers_user
        )
        assert response.status_code == 200
        paginated = response.json
        assert paginated["per_page"] == 1
        assert len(paginated["data"]) <= 1

    def test_error_handling_workflow(self, client, auth_headers_user):
        """Test various error scenarios"""
        # 1. Test unauthorized access
        response = client.get("/api/v1/script")
        assert response.status_code == 401

        # 2. Test forbidden access (regular user accessing admin endpoint)
        response = client.get("/api/v1/user", headers=auth_headers_user)
        assert response.status_code == 403

        # 3. Test not found
        fake_id = "12345678-1234-1234-1234-123456789012"
        response = client.get(f"/api/v1/script/{fake_id}", headers=auth_headers_user)
        assert response.status_code == 404

        # 4. Test bad request (invalid data)
        response = client.post("/api/v1/user", json={"email": "invalid-email"})
        assert response.status_code == 400

        # 5. Test authentication failure
        response = client.post(
            "/auth", json={"email": "nonexistent@test.com", "password": "wrongpassword"}
        )
        assert response.status_code == 401
