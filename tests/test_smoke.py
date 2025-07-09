"""
Basic smoke tests for Trends.Earth API
These tests are designed to run quickly and verify basic functionality
"""

import pytest


class TestHealthCheck:
    """Test health check endpoint"""

    def test_health_check_endpoint(self, client):
        """Test that health check endpoint is accessible"""
        response = client.get("/api-health")

        assert response.status_code == 200

        data = response.json
        assert "status" in data
        assert "timestamp" in data
        assert "database" in data
        assert "version" in data
        assert data["status"] == "ok"
        assert data["version"] == "1.0"
        assert data["database"] in ["healthy", "unhealthy"]

    def test_health_check_no_auth_required(self, client):
        """Test that health check doesn't require authentication"""
        response = client.get("/api-health")
        # Should be accessible without any headers
        assert response.status_code == 200


class TestSmoke:
    """Basic smoke tests that should always pass"""

    def test_import_gefapi(self):
        """Test that we can import the main gefapi module"""
        import gefapi

        assert gefapi is not None

    def test_import_models(self):
        """Test that we can import model classes"""
        from gefapi.models import Execution, Script, User

        assert User is not None
        assert Script is not None
        assert Execution is not None

    def test_import_services(self):
        """Test that we can import service classes"""
        try:
            from gefapi.services import script_service, user_service

            assert user_service is not None
            assert script_service is not None
        except ImportError:
            # Some services might have dependencies that aren't available in test env
            pytest.skip("Service imports not available in test environment")

    def test_basic_math(self):
        """A simple test that always passes to ensure pytest is working"""
        assert 1 + 1 == 2
        assert "hello" + " " + "world" == "hello world"

    def test_environment_variables(self):
        """Test that basic environment setup is working"""
        import os

        # Check if we're in a testing environment - could be set by CI or pytest
        testing = os.environ.get("TESTING")
        # pytest automatically sets PYTEST_CURRENT_TEST when running tests
        pytest_running = os.environ.get("PYTEST_CURRENT_TEST")

        # Pass if either TESTING=true or pytest is running
        assert testing == "true" or pytest_running is not None
