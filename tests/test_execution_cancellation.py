"""Tests for execution cancellation functionality"""

from unittest.mock import Mock, patch

import pytest

from gefapi.errors import ExecutionNotFound
from gefapi.services.execution_service import ExecutionService
from gefapi.services.gee_service import GEEService


class TestExecutionCancellation:
    """Test execution cancellation functionality"""

    def test_extract_gee_task_ids_from_logs(self):
        """Test GEE task ID extraction from logs"""
        logs = [
            "2025-08-06 09:26 EDT - DEBUG - Backing off 1.6 seconds after 2 tries calling function .get_status at 0x711a135c5300> for task 6CIGR7EG2J45GJ2DN2J7X3WZ",
            "2025-08-06 09:26 EDT - DEBUG - Starting GEE task YBKKBHM2V63JYBVIPCCRY7A2",
            "Some other log entry without task ID",
            "2025-08-06 09:26 EDT - DEBUG - Backing off 1.5 seconds after 1 tries calling function .get_status for task 6CIGR7EG2J45GJ2DN2J7X3WZ",
            "Invalid task ID: ABC123",  # Too short
        ]

        task_ids = GEEService.extract_gee_task_ids_from_logs(logs)

        # Should find the two unique task IDs
        assert len(task_ids) == 2
        assert "6CIGR7EG2J45GJ2DN2J7X3WZ" in task_ids
        assert "YBKKBHM2V63JYBVIPCCRY7A2" in task_ids

    @patch("gefapi.services.gee_service.requests.get")
    @patch("gefapi.services.gee_service.requests.post")
    def test_cancel_gee_task_success(self, mock_post, mock_get):
        """Test successful GEE task cancellation"""
        # Mock successful status check
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"metadata": {"state": "RUNNING"}}

        # Mock successful cancellation
        mock_post.return_value.status_code = 200

        with patch.object(
            GEEService, "get_gee_service_account_token", return_value="mock_token"
        ):
            result = GEEService.cancel_gee_task("6CIGR7EG2J45GJ2DN2J7X3WZ")

        assert result["success"] is True
        assert result["task_id"] == "6CIGR7EG2J45GJ2DN2J7X3WZ"
        assert result["status"] == "CANCELLED"

    @patch("gefapi.services.gee_service.requests.get")
    def test_cancel_gee_task_already_completed(self, mock_get):
        """Test GEE task cancellation when task is already completed"""
        # Mock status check showing task already completed
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"metadata": {"state": "SUCCEEDED"}}

        with patch.object(
            GEEService, "get_gee_service_account_token", return_value="mock_token"
        ):
            result = GEEService.cancel_gee_task("6CIGR7EG2J45GJ2DN2J7X3WZ")

        assert result["success"] is True
        assert result["status"] == "SUCCEEDED"
        assert "already in SUCCEEDED state" in result["error"]

    def test_cancel_gee_task_no_token(self):
        """Test GEE task cancellation when no access token is available"""
        with patch.object(
            GEEService, "get_gee_service_account_token", return_value=None
        ):
            result = GEEService.cancel_gee_task("6CIGR7EG2J45GJ2DN2J7X3WZ")

        assert result["success"] is False
        assert "Failed to get GEE access token" in result["error"]

    @patch("gefapi.services.execution_service.ExecutionLog")
    @patch("gefapi.services.execution_service.get_docker_client")
    @patch.object(GEEService, "cancel_gee_tasks_from_execution")
    def test_cancel_execution_with_docker_and_gee(
        self, mock_gee_cancel, mock_docker_client, mock_execution_log
    ):
        """Test execution cancellation with both Docker and GEE task cancellation"""
        from gefapi.models import Execution

        # Mock execution
        mock_execution = Mock(spec=Execution)
        mock_execution.id = "test-execution-id"
        mock_execution.status = "RUNNING"
        mock_execution.user_id = "test-user-id"

        # Mock logs
        mock_log = Mock()
        mock_log.text = "Starting GEE task 6CIGR7EG2J45GJ2DN2J7X3WZ"
        mock_execution_log.query.filter.return_value.order_by.return_value.all.return_value = [
            mock_log
        ]

        # Mock Docker client
        mock_docker = Mock()
        mock_service = Mock()
        mock_service.name = "execution-test-execution-id"
        mock_docker.services.list.return_value = [mock_service]
        mock_docker.containers.list.return_value = []
        mock_docker_client.return_value = mock_docker

        # Mock GEE cancellation
        mock_gee_cancel.return_value = [
            {
                "task_id": "6CIGR7EG2J45GJ2DN2J7X3WZ",
                "success": True,
                "status": "CANCELLED",
                "error": None,
            }
        ]

        with (
            patch.object(
                ExecutionService, "get_execution", return_value=mock_execution
            ),
            patch("gefapi.services.execution_service.db") as mock_db,
        ):
            ExecutionService.cancel_execution("test-execution-id")

        # Verify Docker service was removed
        mock_service.remove.assert_called_once()

        # Verify GEE tasks were cancelled
        mock_gee_cancel.assert_called_once()

        # Verify execution was updated
        assert mock_execution.status == "CANCELLED"
        assert mock_execution.end_date is not None
        assert mock_execution.progress == 100

        # Verify database operations
        mock_db.session.add.assert_called()
        mock_db.session.commit.assert_called()

    def test_cancel_execution_not_found(self):
        """Test cancelling non-existent execution"""
        with (
            patch.object(
                ExecutionService,
                "get_execution",
                side_effect=ExecutionNotFound("Not found"),
            ),
            pytest.raises(ExecutionNotFound),
        ):
            ExecutionService.cancel_execution("non-existent-id")

    def test_cancel_execution_already_finished(self):
        """Test cancelling already finished execution"""
        from gefapi.models import Execution

        mock_execution = Mock(spec=Execution)
        mock_execution.status = "FINISHED"

        with (
            patch.object(
                ExecutionService, "get_execution", return_value=mock_execution
            ),
            pytest.raises(Exception, match="Cannot cancel execution in FINISHED state"),
        ):
            ExecutionService.cancel_execution("finished-execution-id")
