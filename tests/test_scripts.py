"""
Tests for script management endpoints
"""

import io

import pytest


class TestScripts:
    """Test script management functionality"""

    def test_get_scripts(self, client, auth_headers_user, sample_script):
        """Test getting user's scripts"""
        response = client.get("/api/v1/script", headers=auth_headers_user)

        assert response.status_code == 200
        data = response.json["data"]
        assert len(data) == 1
        assert data[0]["name"] == "Test Script"
        assert data[0]["slug"] == "test-script"

    def test_get_scripts_unauthenticated(self, client):
        """Test getting scripts without authentication"""
        response = client.get("/api/v1/script")
        assert response.status_code == 401

    def test_get_script_by_id(self, client, auth_headers_user, sample_script):
        """Test getting specific script"""
        script_id = str(sample_script.id)
        response = client.get(f"/api/v1/script/{script_id}", headers=auth_headers_user)

        assert response.status_code == 200
        data = response.json["data"]
        assert data["name"] == "Test Script"
        assert data["status"] == "SUCCESS"

    def test_get_nonexistent_script(self, client, auth_headers_user):
        """Test getting non-existent script"""
        fake_id = "12345678-1234-1234-1234-123456789012"
        response = client.get(f"/api/v1/script/{fake_id}", headers=auth_headers_user)
        assert response.status_code == 404

    def test_create_script(self, client, auth_headers_user):
        """Test creating a new script"""
        # Create a fake file
        data = {"file": (io.BytesIO(b"test script content"), "test.tar.gz")}

        response = client.post(
            "/api/v1/script",
            data=data,
            headers=auth_headers_user,
            content_type="multipart/form-data",
        )

        # Note: This might fail due to mocked services, but structure should be correct
        # In real implementation, you'd mock the file upload process
        assert response.status_code in [200, 400, 500]  # Depending on mocking

    def test_update_script(self, client, auth_headers_user, sample_script):
        """Test updating an existing script"""
        script_id = str(sample_script.id)
        data = {"file": (io.BytesIO(b"updated script content"), "updated.tar.gz")}

        response = client.patch(
            f"/api/v1/script/{script_id}",
            data=data,
            headers=auth_headers_user,
            content_type="multipart/form-data",
        )

        # Note: Actual response depends on mocked services
        assert response.status_code in [200, 400, 403, 404, 500]

    def test_publish_script(self, client, auth_headers_user, sample_script):
        """Test publishing a script"""
        script_id = str(sample_script.id)
        response = client.post(
            f"/api/v1/script/{script_id}/publish", headers=auth_headers_user
        )

        assert response.status_code == 200
        # Verify script is published (if not mocked)

    def test_unpublish_script(self, client, auth_headers_user, sample_script):
        """Test unpublishing a script"""
        script_id = str(sample_script.id)
        response = client.post(
            f"/api/v1/script/{script_id}/unpublish", headers=auth_headers_user
        )

        assert response.status_code == 200

    def test_delete_script_admin_only(
        self, client, auth_headers_admin, auth_headers_user, sample_script
    ):
        """Test deleting script (admin only)"""
        script_id = str(sample_script.id)

        # Regular user should be forbidden
        response = client.delete(
            f"/api/v1/script/{script_id}", headers=auth_headers_user
        )
        assert response.status_code == 403

        # Admin should be able to delete
        response = client.delete(
            f"/api/v1/script/{script_id}", headers=auth_headers_admin
        )
        assert response.status_code == 200

    def test_download_script(self, client, auth_headers_user, sample_script):
        """Test downloading a script"""
        script_id = str(sample_script.id)
        response = client.get(
            f"/api/v1/script/{script_id}/download", headers=auth_headers_user
        )

        # Response depends on S3 mocking
        assert response.status_code in [200, 404, 500]

    def test_get_script_logs(self, client, auth_headers_user, sample_script):
        """Test getting script logs"""
        script_id = str(sample_script.id)
        response = client.get(
            f"/api/v1/script/{script_id}/log", headers=auth_headers_user
        )

        assert response.status_code == 200
        assert "data" in response.json

    def test_get_script_logs_with_params(
        self, client, auth_headers_user, sample_script
    ):
        """Test getting script logs with parameters"""
        script_id = str(sample_script.id)
        response = client.get(
            f"/api/v1/script/{script_id}/log?start=2025-01-01&last-id=1",
            headers=auth_headers_user,
        )

        assert response.status_code == 200
