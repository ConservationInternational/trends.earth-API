"""
Basic smoke tests for Trends.Earth API
These tests are designed to run quickly and verify basic functionality
"""

import pytest


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

        # These should be set by the CI environment
        testing = os.environ.get("TESTING")
        assert testing == "true"
