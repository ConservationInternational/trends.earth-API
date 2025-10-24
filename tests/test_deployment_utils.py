#!/usr/bin/env python3
"""
Unit tests for deployment utilities.

Tests the DeploymentUtils class methods used in CI/CD workflows to ensure
reliable deployment operations.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

# Import deployment_utils from root directory (same level as run_db_migrations.py)
from deployment_utils import DeploymentUtils


class TestDeploymentUtils:
    """Test cases for DeploymentUtils class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.utils = DeploymentUtils(
            registry="test-registry.local:5000",
            image_name="trends-earth-api",
            app_path="/opt/trends-earth-api",
        )

    @patch("subprocess.run")
    def test_clean_git_workspace_success(self, mock_run):
        """Test successful git workspace cleaning."""
        mock_run.return_value = MagicMock(returncode=0, stdout="main\nabc123\n")

        result = self.utils.clean_git_workspace()

        assert result is True
        # Should call git clean, git reset, and info commands
        assert mock_run.call_count >= 4  # clean, reset, branch, rev-parse

    @patch("subprocess.run")
    def test_clean_git_workspace_failure(self, mock_run):
        """Test git workspace cleaning failure handling."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git clean")

        result = self.utils.clean_git_workspace()

        assert result is False

    @patch("subprocess.run")
    @patch("time.sleep")
    def test_configure_docker_registry_success(self, mock_sleep, mock_run):
        """Test Docker registry configuration."""
        mock_run.return_value = MagicMock(returncode=0)

        result = self.utils.configure_docker_registry()

        assert result is True
        # Should call mkdir, tee, and systemctl restart
        assert mock_run.call_count >= 3

    @patch("subprocess.run")
    def test_configure_docker_registry_failure(self, mock_run):
        """Test Docker registry configuration failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "mkdir")

        result = self.utils.configure_docker_registry()

        assert result is False

    @patch("subprocess.run")
    def test_build_and_push_image_success(self, mock_run):
        """Test successful Docker image build and push."""
        mock_run.return_value = MagicMock(returncode=0)

        result = self.utils.build_and_push_image(tags=["v1.0.0", "latest", "stable"])

        assert result is True
        # Should call docker build and multiple docker push commands
        assert mock_run.call_count >= 4  # build + 3 pushes

    @patch("subprocess.run")
    def test_build_and_push_image_local_only(self, mock_run):
        """Test Docker image build without registry (local only)."""
        mock_run.return_value = MagicMock(returncode=0)

        # Test with no registry
        utils = DeploymentUtils(image_name="trends-earth-api", registry="")
        result = utils.build_and_push_image(tags=["v1.0.0"])

        assert result is True
        assert mock_run.call_count >= 1  # Should build image

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_check_service_health_success(self, mock_run, mock_sleep):
        """Test successful service health check."""
        # Mock netcat and curl to succeed
        mock_run.side_effect = [
            MagicMock(returncode=0),  # netcat check
            MagicMock(returncode=0, stdout="HTTP_CODE:200"),  # curl check
        ]

        result = self.utils.check_service_health(
            port=3001, path="/api-health", max_attempts=6, wait_seconds=10
        )

        assert result is True

        # Verify multiple calls were made
        assert mock_run.call_count >= 2

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_check_service_health_timeout(self, mock_run, mock_sleep):
        """Test service health check timeout."""
        # Mock netcat to always fail
        mock_run.return_value = MagicMock(returncode=1)

        result = self.utils.check_service_health(
            port=3001, path="/api-health", max_attempts=3, wait_seconds=10
        )

        assert result is False

        # Should have tried multiple times (timeout/15 attempts)
        assert mock_run.call_count >= 2

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_wait_for_services_ready_success(self, mock_run, mock_sleep):
        """Test successful service readiness check."""
        # Mock docker service ls to show services ready after 2 attempts
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="trends-earth-prod_api\t0/1\ntrends-earth-prod_worker\t0/1",
            ),  # Not ready
            MagicMock(
                returncode=0,
                stdout="trends-earth-prod_api\t1/1\ntrends-earth-prod_worker\t1/1",
            ),  # Ready
        ]

        result = self.utils.wait_for_services_ready(
            stack_name="trends-earth-prod", max_wait=60
        )

        assert result is True

        # Should have called docker service ls at least once
        assert mock_run.call_count >= 1

        # Verify the call included check=True parameter
        call_args = mock_run.call_args
        assert call_args.kwargs.get("check") is True

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_wait_for_services_ready_timeout(self, mock_run, mock_sleep):
        """Test service readiness check timeout."""
        # Mock service ls to always show services not ready (more than 2 services)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NAME\tREPLICAS\ntrends-earth-prod_api\t0/1\ntrends-earth-prod_worker\t0/1\ntrends-earth-prod_db\t0/1",
        )

        result = self.utils.wait_for_services_ready(
            stack_name="trends-earth-prod",
            max_wait=1,  # Very short timeout for test
        )

        assert result is False

        # Should have tried at least once
        assert mock_run.call_count >= 1

    @patch("subprocess.run")
    def test_wait_for_services_ready_with_migrate_service(self, mock_run):
        """Test service readiness check accounting for migrate service."""
        # Mock output with migrate service that's expected to be 0/1
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NAME\tREPLICAS\ntrends-earth-prod_api\t1/1\ntrends-earth-prod_migrate\t0/1",
        )

        result = self.utils.wait_for_services_ready(
            stack_name="trends-earth-prod", max_wait=60
        )

        assert result is True

    def test_deployment_utils_basic(self):
        """Test basic deployment utils functionality."""
        # Test that the utils object was created correctly
        assert self.utils.image_name == "trends-earth-api"
        assert self.utils.registry == "test-registry.local:5000"


class TestDeploymentUtilsCLI:
    """Test cases for deployment_utils CLI interface."""

    @patch(
        "sys.argv",
        [
            "deployment_utils.py",
            "--registry",
            "test-reg",
            "--image-name",
            "test-img",
            "clean-git",
        ],
    )
    @patch("deployment_utils.DeploymentUtils.clean_git_workspace")
    def test_cli_clean_git(self, mock_clean):
        """Test CLI clean-git command."""
        mock_clean.return_value = True

        # Import and run main function
        from deployment_utils import main

        try:
            main()
        except SystemExit as e:
            assert e.code == 0

        mock_clean.assert_called_once()

    @patch(
        "sys.argv",
        [
            "deployment_utils.py",
            "--registry",
            "test-reg",
            "--image-name",
            "test-img",
            "configure-registry",
        ],
    )
    @patch("deployment_utils.DeploymentUtils.configure_docker_registry")
    def test_cli_configure_registry(self, mock_configure):
        """Test CLI configure-registry command."""
        mock_configure.return_value = True

        from deployment_utils import main

        result = main()

        assert result == 0
        mock_configure.assert_called_once()

    @patch(
        "sys.argv",
        [
            "deployment_utils.py",
            "--registry",
            "test-reg",
            "--image-name",
            "test-img",
            "build-image",
            "--tags",
            "v1.0.0",
            "latest",
            "stable",
        ],
    )
    @patch("deployment_utils.DeploymentUtils.build_and_push_image")
    def test_cli_build_and_push(self, mock_build):
        """Test CLI build-image command."""
        mock_build.return_value = True

        from deployment_utils import main

        result = main()

        assert result == 0
        mock_build.assert_called_once()

    @patch(
        "sys.argv",
        [
            "deployment_utils.py",
            "--registry",
            "test-reg",
            "health-check",
            "--port",
            "3001",
            "--path",
            "/health",
        ],
    )
    @patch("deployment_utils.DeploymentUtils.check_service_health")
    def test_cli_check_health(self, mock_health):
        """Test CLI health-check command."""
        mock_health.return_value = True

        from deployment_utils import main

        result = main()

        assert result == 0
        mock_health.assert_called_once()

    @patch(
        "sys.argv",
        [
            "deployment_utils.py",
            "--registry",
            "test-reg",
            "wait-services",
            "--stack-name",
            "test-stack",
            "--max-wait",
            "180",
        ],
    )
    @patch("deployment_utils.DeploymentUtils.wait_for_services_ready")
    def test_cli_wait_for_services(self, mock_wait):
        """Test CLI wait-services command."""
        mock_wait.return_value = True

        from deployment_utils import main

        result = main()

        assert result == 0
        mock_wait.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__])
