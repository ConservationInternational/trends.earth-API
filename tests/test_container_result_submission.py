"""
Test for container result submission endpoint

This test validates that containers can submit results back to the API
without authentication using the /execution/<id>/update-from-container endpoint.
"""
import json
import pytest
from unittest.mock import patch
from gefapi.models import Execution, Script, User
from gefapi.services.execution_service import ExecutionService


@pytest.mark.usefixtures("client", "auth_headers_admin")
class TestContainerResultSubmission:
    """Test container result submission functionality"""

    def test_update_execution_from_container_success(self, client, auth_headers_admin):
        """Test successful result submission from container"""
        # First, create a script and execution to test with
        script_data = {
            "name": "Test Container Script",
            "slug": "test-container-script",
            "type": "PYTHON",
            "source_code": "print('Hello from container')",
            "public": True
        }
        
        # Create script via API
        script_response = client.post(
            "/api/v1/script",
            data=json.dumps(script_data),
            headers=auth_headers_admin,
            content_type="application/json"
        )
        assert script_response.status_code == 201
        script_id = script_response.json["data"]["id"]
        
        # Create execution via API (this will also create a database record)
        # We'll mock the docker execution to avoid actually running containers
        with patch('gefapi.tasks.docker.docker_run.delay') as mock_docker:
            execution_response = client.post(
                f"/api/v1/script/{script_id}/run",
                data=json.dumps({"params": {}}),
                headers=auth_headers_admin,
                content_type="application/json"
            )
            assert execution_response.status_code == 200
            execution_id = execution_response.json["data"]["id"]
        
        # Now test the container result submission endpoint
        container_results = {
            "results": {
                "output_files": ["output.tif", "summary.json"],
                "metrics": {
                    "area_processed": 1000,
                    "processing_time": 45.2
                },
                "outputs": {
                    "ndvi_mean": 0.75,
                    "degradation_pct": 12.5
                }
            },
            "status": "FINISHED",
            "message": "Script completed successfully"
        }
        
        # Submit results from container (no auth headers - simulating container call)
        result_response = client.post(
            f"/api/v1/execution/{execution_id}/update-from-container",
            data=json.dumps(container_results),
            content_type="application/json"
        )
        
        assert result_response.status_code == 200
        response_data = result_response.json["data"]
        
        # Verify the execution was updated
        assert response_data["id"] == execution_id
        assert response_data["status"] == "FINISHED"
        assert response_data["results"] == container_results["results"]
        
        # Verify in database
        updated_execution = ExecutionService.get_execution(execution_id)
        assert updated_execution.status == "FINISHED"
        assert updated_execution.results == container_results["results"]

    def test_update_execution_from_container_missing_results(self, client):
        """Test that endpoint requires results field"""
        # Use a dummy execution ID (endpoint should validate results before checking existence)
        execution_id = "550e8400-e29b-41d4-a716-446655440000"
        
        # Missing results field
        invalid_data = {
            "status": "FINISHED",
            "message": "No results provided"
        }
        
        response = client.post(
            f"/api/v1/execution/{execution_id}/update-from-container",
            data=json.dumps(invalid_data),
            content_type="application/json"
        )
        
        assert response.status_code == 400
        assert "must include 'results' field" in response.json["error"]

    def test_update_execution_from_container_execution_not_found(self, client):
        """Test that endpoint handles non-existent execution gracefully"""
        execution_id = "550e8400-e29b-41d4-a716-446655440000"  # Non-existent ID
        
        container_results = {
            "results": {
                "output_files": ["test.tif"],
                "metrics": {"area": 500}
            }
        }
        
        response = client.post(
            f"/api/v1/execution/{execution_id}/update-from-container",
            data=json.dumps(container_results),
            content_type="application/json"
        )
        
        assert response.status_code == 404
        assert "not found" in response.json["error"].lower()

    def test_update_execution_from_container_minimal_payload(self, client, auth_headers_admin):
        """Test endpoint with minimal required payload (only results)"""
        # Create a script and execution
        script_data = {
            "name": "Minimal Test Script",
            "slug": "minimal-test-script", 
            "type": "PYTHON",
            "source_code": "print('Minimal test')",
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
        
        # Submit minimal payload (only results)
        minimal_results = {
            "results": {
                "output": "success"
            }
        }
        
        response = client.post(
            f"/api/v1/execution/{execution_id}/update-from-container",
            data=json.dumps(minimal_results),
            content_type="application/json"
        )
        
        assert response.status_code == 200
        response_data = response.json["data"]
        assert response_data["results"] == minimal_results["results"]
        # Status should remain unchanged if not provided
        assert response_data["status"] == "RUNNING"  # Default status
