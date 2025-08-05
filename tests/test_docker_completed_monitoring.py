"""Test docker completed monitoring functionality"""

import datetime
from unittest.mock import MagicMock, patch

from gefapi.tasks.docker_completed_monitoring import (
    monitor_completed_docker_services,
)


class TestDockerCompletedMonitoring:
    """Test docker completed service monitoring functionality"""

    @patch("gefapi.tasks.docker_completed_monitoring.get_docker_client")
    def test_monitor_completed_docker_services_basic_functionality(
        self, mock_get_docker_client, app, db_session, sample_execution
    ):
        """Test basic monitoring task functionality"""
        execution_id = sample_execution.id

        # Set execution to FINISHED status (already completed in database)
        sample_execution.status = "FINISHED"
        sample_execution.end_date = datetime.datetime.utcnow() - datetime.timedelta(
            minutes=30
        )
        sample_execution.start_date = datetime.datetime.utcnow() - datetime.timedelta(
            hours=1
        )
        db_session.add(sample_execution)
        db_session.commit()

        # Mock Docker client and service
        mock_client = MagicMock()
        mock_get_docker_client.return_value = mock_client

        # Mock a lingering Docker service for the completed execution
        mock_service = MagicMock()
        mock_service.name = f"execution-{execution_id}"

        # Mock services.list to return our service only when filtered by the right name
        def mock_services_list(filters=None):
            if filters and filters.get("name") == f"execution-{execution_id}":
                return [mock_service]
            return []

        mock_client.services.list.side_effect = mock_services_list

        # Run the monitoring task
        with app.app_context():
            result = monitor_completed_docker_services.apply().result

        # Verify the task ran and found the lingering service
        assert result["checked"] >= 1
        assert result["completed_services_found"] >= 1
        assert result["services_removed"] >= 1

        # Verify Docker service was removed
        mock_service.remove.assert_called_once()

    @patch("gefapi.tasks.docker_completed_monitoring.get_docker_client")
    def test_monitor_completed_docker_services_no_services(
        self, mock_get_docker_client, app, db_session, sample_execution
    ):
        """Test monitoring when no lingering services exist"""
        # Set execution to FINISHED status
        sample_execution.status = "FINISHED"
        sample_execution.end_date = datetime.datetime.utcnow() - datetime.timedelta(
            minutes=30
        )
        sample_execution.start_date = datetime.datetime.utcnow() - datetime.timedelta(
            hours=1
        )
        db_session.add(sample_execution)
        db_session.commit()

        # Mock Docker client with no services
        mock_client = MagicMock()
        mock_get_docker_client.return_value = mock_client

        # Mock services.list to return empty list for any filter
        mock_client.services.list.return_value = []

        # Run the monitoring task
        with app.app_context():
            result = monitor_completed_docker_services.apply().result

        # Should find no lingering services
        assert result["completed_services_found"] == 0
        assert result["services_removed"] == 0

    @patch("gefapi.tasks.docker_completed_monitoring.get_docker_client")
    def test_monitor_docker_unavailable(self, mock_get_docker_client, app):
        """Test monitoring when Docker is unavailable"""
        # Mock Docker client as unavailable
        mock_get_docker_client.return_value = None

        # Run the monitoring task
        with app.app_context():
            result = monitor_completed_docker_services.apply().result

        # Should handle Docker unavailability gracefully
        assert result["checked"] == 0
        assert result["error"] == "Docker unavailable"

    @patch("gefapi.tasks.docker_completed_monitoring.get_docker_client")
    def test_monitor_service_removal_error(
        self, mock_get_docker_client, app, db_session, sample_execution
    ):
        """Test monitoring when service removal fails"""
        # Set execution to FINISHED status
        sample_execution.status = "FINISHED"
        sample_execution.end_date = datetime.datetime.utcnow() - datetime.timedelta(
            minutes=30
        )
        sample_execution.start_date = datetime.datetime.utcnow() - datetime.timedelta(
            hours=1
        )
        db_session.add(sample_execution)
        db_session.commit()

        # Mock Docker client and service
        mock_client = MagicMock()
        mock_get_docker_client.return_value = mock_client

        # Mock a service that fails to remove
        mock_service = MagicMock()
        mock_service.remove.side_effect = Exception("Docker remove error")

        # Mock services.list to return our service only when filtered by the right name
        def mock_services_list(filters=None):
            if filters and filters.get("name") == f"execution-{sample_execution.id}":
                return [mock_service]
            return []

        mock_client.services.list.side_effect = mock_services_list

        # Run the monitoring task
        with app.app_context():
            result = monitor_completed_docker_services.apply().result

        # Should detect the service but fail to remove it
        assert result["completed_services_found"] >= 1
        assert result["services_removed"] == 0  # Failed to remove

        # Verify removal was attempted
        mock_service.remove.assert_called_once()

    def test_monitor_ignores_running_executions(
        self, app, db_session, sample_execution
    ):
        """Test that monitoring ignores running executions"""
        # Set execution to RUNNING status (should be ignored)
        sample_execution.status = "RUNNING"
        sample_execution.start_date = datetime.datetime.utcnow() - datetime.timedelta(
            hours=1
        )
        db_session.add(sample_execution)
        db_session.commit()

        # Run the monitoring task
        with app.app_context():
            result = monitor_completed_docker_services.apply().result

        # Should not check running executions
        assert result["checked"] == 0
