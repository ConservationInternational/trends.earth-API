"""
Test for execution result submission fix

This test validates that users can now update their own executions with results,
fixing the issue where containers couldn't submit results due to auth restrictions.
"""
import json
from unittest.mock import patch

import pytest


@pytest.mark.usefixtures("client", "auth_headers_user", "auth_headers_admin")
class TestExecutionUpdateAuthFix:
    """Test that the execution update auth fix works correctly"""

    def test_user_can_update_own_execution(self, client, auth_headers_user):
        """Test that a user can update their own execution with results"""
        # First, create a script and execution as a regular user
        script_data = {
            "name": "Test Results Script",
            "slug": "test-results-script",
            "type": "PYTHON",
            "source_code": "print('Test results submission')",
            "public": True
        }
        
        # Create script
        script_response = client.post(
            "/api/v1/script",
            data=json.dumps(script_data),
            headers=auth_headers_user,
            content_type="application/json"
        )
        assert script_response.status_code == 201
        script_id = script_response.json["data"]["id"]
        
        # Create execution (mocking docker to avoid actual container execution)
        with patch('gefapi.tasks.docker.docker_run.delay'):
            execution_response = client.post(
                f"/api/v1/script/{script_id}/run",
                data=json.dumps({"params": {}}),
                headers=auth_headers_user,
                content_type="application/json"
            )
            assert execution_response.status_code == 200
            execution_id = execution_response.json["data"]["id"]
        
        # Now test that the user can update their own execution with results
        results_data = {
            "results": {
                "output_files": ["result.tif", "summary.json"],
                "metrics": {
                    "area_processed": 1000,
                    "processing_time": 45.2
                },
                "status": "success"
            },
            "status": "FINISHED"
        }
        
        # User updating their own execution - should succeed now
        update_response = client.patch(
            f"/api/v1/execution/{execution_id}",
            data=json.dumps(results_data),
            headers=auth_headers_user,
            content_type="application/json"
        )
        
        assert update_response.status_code == 200
        response_data = update_response.json["data"]
        
        # Verify the execution was updated with results
        assert response_data["id"] == execution_id
        assert response_data["status"] == "FINISHED"
        assert response_data["results"] == results_data["results"]
        
        # Verify in database by fetching the execution again
        get_response = client.get(
            f"/api/v1/execution/{execution_id}",
            headers=auth_headers_user
        )
        assert get_response.status_code == 200
        fetched_execution = get_response.json["data"]
        assert fetched_execution["results"] == results_data["results"]

    def test_user_cannot_update_other_users_execution(self, client, auth_headers_user, auth_headers_admin):
        """Test that a user cannot update another user's execution"""
        # Admin creates a script and execution
        script_data = {
            "name": "Admin Test Script",
            "slug": "admin-test-script",
            "type": "PYTHON",
            "source_code": "print('Admin script')",
            "public": True
        }
        
        script_response = client.post(
            "/api/v1/script",
            data=json.dumps(script_data),
            headers=auth_headers_admin,
            content_type="application/json"
        )
        script_id = script_response.json["data"]["id"]
        
        with patch('gefapi.tasks.docker.docker_run.delay'):
            execution_response = client.post(
                f"/api/v1/script/{script_id}/run",
                data=json.dumps({"params": {}}),
                headers=auth_headers_admin,
                content_type="application/json"
            )
            execution_id = execution_response.json["data"]["id"]
        
        # Regular user tries to update admin's execution - should fail
        results_data = {
            "results": {"unauthorized": "attempt"},
            "status": "FINISHED"
        }
        
        update_response = client.patch(
            f"/api/v1/execution/{execution_id}",
            data=json.dumps(results_data),
            headers=auth_headers_user,  # Regular user trying to update admin execution
            content_type="application/json"
        )
        
        assert update_response.status_code == 403
        assert "Forbidden" in update_response.json["error"]

    def test_admin_can_update_any_execution(self, client, auth_headers_user, auth_headers_admin):
        """Test that admin can still update any execution"""
        # Regular user creates execution
        script_data = {
            "name": "User Test Script",
            "slug": "user-test-script",
            "type": "PYTHON",
            "source_code": "print('User script')",
            "public": True
        }
        
        script_response = client.post(
            "/api/v1/script",
            data=json.dumps(script_data),
            headers=auth_headers_user,
            content_type="application/json"
        )
        script_id = script_response.json["data"]["id"]
        
        with patch('gefapi.tasks.docker.docker_run.delay'):
            execution_response = client.post(
                f"/api/v1/script/{script_id}/run",
                data=json.dumps({"params": {}}),
                headers=auth_headers_user,
                content_type="application/json"
            )
            execution_id = execution_response.json["data"]["id"]
        
        # Admin updates user's execution - should succeed
        results_data = {
            "results": {"admin_update": "allowed"},
            "status": "FINISHED"
        }
        
        update_response = client.patch(
            f"/api/v1/execution/{execution_id}",
            data=json.dumps(results_data),
            headers=auth_headers_admin,  # Admin updating user execution
            content_type="application/json"
        )
        
        assert update_response.status_code == 200
        response_data = update_response.json["data"]
        assert response_data["results"] == results_data["results"]
