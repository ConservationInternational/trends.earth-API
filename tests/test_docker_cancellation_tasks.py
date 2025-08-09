"""Tests for Docker service cancellation Celery tasks"""

from unittest.mock import Mock, patch


class TestDockerCancellationTasks:
    """Test Docker service cancellation Celery tasks"""

    @patch("gefapi.services.docker_service.get_docker_client")
    def test_cancel_execution_task_service_success(self, mock_get_docker_client):
        """Test successful Docker service cancellation via Celery task"""
        from gefapi.services.docker_service import cancel_execution_task

        # Mock Docker client and service
        mock_docker = Mock()
        mock_service = Mock()
        mock_service.name = "execution-test-execution-id"
        mock_docker.services.list.return_value = [mock_service]
        mock_docker.containers.list.return_value = []
        mock_get_docker_client.return_value = mock_docker

        # Execute the task
        result = cancel_execution_task("test-execution-id")

        # Verify service was removed
        mock_service.remove.assert_called_once()

        # Verify result structure
        assert result["docker_service_stopped"] is True
        assert result["docker_container_stopped"] is False
        assert len(result["errors"]) == 0

    @patch("gefapi.services.docker_service.get_docker_client")
    def test_cancel_execution_task_container_success(self, mock_get_docker_client):
        """Test successful Docker container cancellation via Celery task"""
        from gefapi.services.docker_service import cancel_execution_task

        # Mock Docker client and container
        mock_docker = Mock()
        mock_container = Mock()
        mock_container.name = "execution-test-execution-id"
        mock_container.status = "running"  # Container must be running to be stopped
        mock_docker.services.list.return_value = []
        mock_docker.containers.list.return_value = [mock_container]
        mock_get_docker_client.return_value = mock_docker

        # Execute the task
        result = cancel_execution_task("test-execution-id")

        # Verify container was stopped and removed
        mock_container.stop.assert_called_once_with(timeout=10)
        mock_container.remove.assert_called_once_with(force=True)

        # Verify result structure
        assert result["docker_service_stopped"] is False
        assert result["docker_container_stopped"] is True
        assert len(result["errors"]) == 0

    @patch("gefapi.services.docker_service.get_docker_client")
    def test_cancel_execution_task_both_service_and_container(
        self, mock_get_docker_client
    ):
        """Test cancellation when both service and container exist"""
        from gefapi.services.docker_service import cancel_execution_task

        # Mock Docker client with both service and container
        mock_docker = Mock()
        mock_service = Mock()
        mock_service.name = "execution-test-execution-id"
        mock_container = Mock()
        mock_container.name = "execution-test-execution-id"
        mock_container.status = "running"  # Container must be running to be stopped

        mock_docker.services.list.return_value = [mock_service]
        mock_docker.containers.list.return_value = [mock_container]
        mock_get_docker_client.return_value = mock_docker

        # Execute the task
        result = cancel_execution_task("test-execution-id")

        # Verify both were handled
        mock_service.remove.assert_called_once()
        mock_container.stop.assert_called_once_with(timeout=10)
        mock_container.remove.assert_called_once_with(force=True)

        # Verify result structure
        assert result["docker_service_stopped"] is True
        assert result["docker_container_stopped"] is True
        assert len(result["errors"]) == 0

    @patch("gefapi.services.docker_service.get_docker_client")
    def test_cancel_execution_task_no_resources_found(self, mock_get_docker_client):
        """Test task when no Docker resources are found"""
        from gefapi.services.docker_service import cancel_execution_task

        # Mock Docker client with no matching resources
        mock_docker = Mock()
        mock_docker.services.list.return_value = []
        mock_docker.containers.list.return_value = []
        mock_get_docker_client.return_value = mock_docker

        # Execute the task
        result = cancel_execution_task("test-execution-id")

        # Verify result indicates no resources found
        assert result["docker_service_stopped"] is False
        assert result["docker_container_stopped"] is False
        assert len(result["errors"]) == 0

    @patch("gefapi.services.docker_service.get_docker_client")
    def test_cancel_execution_task_service_removal_error(self, mock_get_docker_client):
        """Test task when service removal fails"""
        from gefapi.services.docker_service import cancel_execution_task

        # Mock Docker client with service that fails to remove
        mock_docker = Mock()
        mock_service = Mock()
        mock_service.name = "execution-test-execution-id"
        mock_service.remove.side_effect = Exception("Service removal failed")
        mock_docker.services.list.return_value = [mock_service]
        mock_docker.containers.list.return_value = []
        mock_get_docker_client.return_value = mock_docker

        # Execute the task
        result = cancel_execution_task("test-execution-id")

        # Verify error is captured
        assert result["docker_service_stopped"] is False
        assert result["docker_container_stopped"] is False
        assert len(result["errors"]) == 1
        assert "Service removal failed" in result["errors"][0]

    @patch("gefapi.services.docker_service.get_docker_client")
    def test_cancel_execution_task_container_stop_error(self, mock_get_docker_client):
        """Test task when container stop fails"""
        from gefapi.services.docker_service import cancel_execution_task

        # Mock Docker client with container that fails to stop
        mock_docker = Mock()
        mock_container = Mock()
        mock_container.name = "execution-test-execution-id"
        mock_container.status = "running"
        mock_container.stop.side_effect = Exception("Container stop failed")
        mock_docker.services.list.return_value = []
        mock_docker.containers.list.return_value = [mock_container]
        mock_get_docker_client.return_value = mock_docker

        # Execute the task
        result = cancel_execution_task("test-execution-id")

        # Verify error is captured and container operations failed
        assert result["docker_service_stopped"] is False
        assert result["docker_container_stopped"] is False
        assert len(result["errors"]) >= 1
        assert any("Container stop failed" in error for error in result["errors"])

        # Container stop should be attempted, but remove won't be called due to exception
        mock_container.stop.assert_called_once_with(timeout=10)
        # Remove should NOT be called since the exception stops the flow
        mock_container.remove.assert_not_called()

    @patch("gefapi.services.docker_service.get_docker_client")
    def test_cancel_execution_task_docker_client_error(self, mock_get_docker_client):
        """Test task when Docker client creation fails"""
        from gefapi.services.docker_service import cancel_execution_task

        # Mock Docker client creation failure
        mock_get_docker_client.return_value = None

        # Execute the task
        result = cancel_execution_task("test-execution-id")

        # Verify error is captured
        assert result["docker_service_stopped"] is False
        assert result["docker_container_stopped"] is False
        assert len(result["errors"]) == 1
        assert "Docker client not available" in result["errors"][0]

    @patch("gefapi.services.docker_service.get_docker_client")
    def test_cancel_execution_task_multiple_matching_services(
        self, mock_get_docker_client
    ):
        """Test task when multiple matching services exist"""
        from gefapi.services.docker_service import cancel_execution_task

        # Mock Docker client with multiple matching services
        mock_docker = Mock()
        mock_service1 = Mock()
        mock_service1.name = "execution-test-execution-id"
        mock_service2 = Mock()
        mock_service2.name = "execution-test-execution-id-worker"

        # Only the exact match should be removed
        mock_docker.services.list.return_value = [mock_service1, mock_service2]
        mock_docker.containers.list.return_value = []
        mock_get_docker_client.return_value = mock_docker

        # Execute the task
        result = cancel_execution_task("test-execution-id")

        # Verify only exact match was removed
        mock_service1.remove.assert_called_once()
        mock_service2.remove.assert_not_called()

        assert result["docker_service_stopped"] is True
        assert result["docker_container_stopped"] is False

    @patch("gefapi.services.docker_service.get_docker_client")
    def test_cancel_execution_task_partial_failure(self, mock_get_docker_client):
        """Test task with mixed success and failure"""
        from gefapi.services.docker_service import cancel_execution_task

        # Mock Docker client with service success and container failure
        mock_docker = Mock()
        mock_service = Mock()
        mock_service.name = "execution-test-execution-id"
        mock_container = Mock()
        mock_container.name = "execution-test-execution-id"
        mock_container.status = "running"
        mock_container.stop.side_effect = Exception("Stop failed")
        mock_container.remove.side_effect = Exception("Remove failed")

        mock_docker.services.list.return_value = [mock_service]
        mock_docker.containers.list.return_value = [mock_container]
        mock_get_docker_client.return_value = mock_docker

        # Execute the task
        result = cancel_execution_task("test-execution-id")

        # Verify mixed results
        assert result["docker_service_stopped"] is True
        assert result["docker_container_stopped"] is False
        assert (
            len(result["errors"]) == 1
        )  # Only one error since container operations are in try-except block
        assert any(
            "Docker container stop failed" in error for error in result["errors"]
        )

        # Service should still have been removed successfully
        mock_service.remove.assert_called_once()

    @patch("gefapi.services.docker_service.get_docker_client")
    @patch("gefapi.services.docker_service.logger")
    def test_cancel_execution_task_logging(self, mock_logger, mock_get_docker_client):
        """Test that appropriate logging occurs during task execution"""
        from gefapi.services.docker_service import cancel_execution_task

        # Mock successful Docker operations
        mock_docker = Mock()
        mock_service = Mock()
        mock_service.name = "execution-test-execution-id"
        mock_docker.services.list.return_value = [mock_service]
        mock_docker.containers.list.return_value = []
        mock_get_docker_client.return_value = mock_docker

        # Execute the task
        cancel_execution_task("test-execution-id")

        # Verify logging calls were made
        assert mock_logger.info.call_count >= 2  # Start and completion logs

        # Check for specific log messages
        log_messages = [call[0][0] for call in mock_logger.info.call_args_list]
        assert any(
            "Celery task: Canceling Docker resources for execution" in msg
            for msg in log_messages
        )
        assert any("Stopping Docker service" in msg for msg in log_messages)

    @patch("gefapi.services.docker_service.get_docker_client")
    def test_cancel_execution_task_container_name_variations(
        self, mock_get_docker_client
    ):
        """Test task with different container naming patterns"""
        from gefapi.services.docker_service import cancel_execution_task

        # Mock containers with different naming patterns
        mock_docker = Mock()
        mock_container1 = Mock()
        mock_container1.name = "execution-test-execution-id"  # Exact match
        mock_container1.status = "running"
        mock_container2 = Mock()
        mock_container2.name = (
            "execution-test-execution-id-something"  # Should not match
        )
        mock_container3 = Mock()
        mock_container3.name = "other-execution-test-execution-id"  # Should not match

        mock_docker.services.list.return_value = []
        # The actual implementation uses filters, so only exact match will be returned
        mock_docker.containers.list.return_value = [mock_container1]  # Only exact match
        mock_get_docker_client.return_value = mock_docker

        # Execute the task
        result = cancel_execution_task("test-execution-id")

        # Verify only exact match was processed
        mock_container1.stop.assert_called_once_with(timeout=10)
        mock_container1.remove.assert_called_once_with(force=True)

        assert result["docker_container_stopped"] is True


class TestDockerServiceTaskIntegration:
    """Integration tests for Docker service task integration"""

    @patch("gefapi.services.docker_service.get_docker_client")
    def test_task_decorator_and_queue_configuration(self, mock_get_docker_client):
        """Test that the Celery task is properly configured"""
        from gefapi.services.docker_service import cancel_execution_task

        # Verify the task has the correct Celery decorator
        assert hasattr(cancel_execution_task, "delay")
        assert hasattr(cancel_execution_task, "apply_async")

        # Check task name (this is set by the Celery decorator)
        # The actual task name is available at runtime when Celery is configured
        # For testing, we just verify the function exists and has Celery attributes

    @patch("gefapi.services.docker_service.get_docker_client")
    @patch("gefapi.services.execution_service.celery_app")
    def test_execution_service_calls_docker_task_correctly(
        self, mock_celery_app, mock_get_docker_client
    ):
        """Test that ExecutionService properly calls the Docker cancellation task"""
        from gefapi.models import Execution
        from gefapi.services.execution_service import ExecutionService

        # Mock execution
        mock_execution = Mock(spec=Execution)
        mock_execution.id = "test-execution-id"
        mock_execution.status = "RUNNING"
        mock_execution.user_id = "test-user-id"

        # Mock successful task result
        mock_task_result = Mock()
        mock_task_result.get.return_value = {
            "docker_service_stopped": True,
            "docker_container_stopped": False,
            "errors": [],
        }
        mock_celery_app.send_task.return_value = mock_task_result

        with (
            patch.object(
                ExecutionService, "get_execution", return_value=mock_execution
            ),
            patch(
                "gefapi.services.execution_service.ExecutionLog"
            ) as mock_execution_log,
            patch(
                "gefapi.services.gee_service.GEEService.cancel_gee_tasks_from_execution"
            ) as mock_gee_cancel,
            patch("gefapi.services.execution_service.db") as mock_db,
        ):
            mock_execution_log.query.filter.return_value.order_by.return_value.all.return_value = []
            mock_gee_cancel.return_value = []

            ExecutionService.cancel_execution("test-execution-id")

            # Verify the correct task was dispatched to the correct queue
            mock_celery_app.send_task.assert_called_once_with(
                "docker.cancel_execution", args=["test-execution-id"], queue="build"
            )

            # Verify task result was retrieved with timeout
            mock_task_result.get.assert_called_once_with(timeout=60)
