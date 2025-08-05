"""Test docker completed monitoring functionality"""

import datetime
from unittest.mock import MagicMock, patch

from gefapi.models import Execution, ExecutionLog
from gefapi.tasks.docker_completed_monitoring import (
    monitor_completed_docker_services,
)


class TestDockerCompletedMonitoring:
    """Test docker completed service monitoring functionality"""

    @patch("gefapi.tasks.docker_completed_monitoring.get_docker_client")
    def test_monitor_completed_docker_services_with_lingering_service(
        self, mock_get_docker_client, app, db_session, sample_execution
    ):
        """Test monitoring task with a completed execution that still has a service"""
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

        # Mock Docker client and service (this service should not exist but does)
        mock_client = MagicMock()
        mock_get_docker_client.return_value = mock_client

        # Mock a lingering Docker service for the completed execution
        mock_service = MagicMock()
        mock_service.name = f"execution-{execution_id}"
        mock_client.services.list.return_value = [mock_service]

        # Run the monitoring task
        with app.app_context():
            result = monitor_completed_docker_services()

        assert result["checked"] >= 1
        assert result["completed_services_found"] == 1
        assert result["executions_marked_finished"] == 0  # Already finished
        assert result["services_removed"] == 1

        # Verify execution status didn't change (already finished)
        with app.app_context():
            updated_execution = Execution.query.get(execution_id)
            assert updated_execution.status == "FINISHED"

        # Verify log entry was created for the cleanup
        log_entries = ExecutionLog.query.filter_by(execution_id=execution_id).all()
        cleanup_log = next(
            (
                log
                for log in log_entries
                if "Lingering Docker service removed" in log.text
            ),
            None,
        )
        assert cleanup_log is not None
        assert cleanup_log.level == "INFO"

        # Verify Docker service was removed
        mock_service.remove.assert_called_once()

    @patch("gefapi.tasks.docker_completed_monitoring.get_docker_client")
    def test_monitor_completed_docker_services_with_failed_execution(
        self, mock_get_docker_client, app, db_session, sample_execution
    ):
        """Test monitoring task with a failed execution that still has a service"""
        execution_id = sample_execution.id

        # Clear any other completed executions from other tests first
        db_session.query(Execution).filter(
            Execution.status.in_(["FAILED", "FINISHED"]), Execution.id != execution_id
        ).delete()
        db_session.commit()

        # Set execution to FAILED status
        sample_execution.status = "FAILED"
        sample_execution.end_date = datetime.datetime.utcnow() - datetime.timedelta(
            minutes=15
        )
        sample_execution.start_date = datetime.datetime.utcnow() - datetime.timedelta(
            hours=1
        )
        db_session.add(sample_execution)
        db_session.commit()

        # Mock Docker client and service
        mock_client = MagicMock()
        mock_get_docker_client.return_value = mock_client

        # Mock a lingering Docker service for the failed execution only
        mock_service = MagicMock()
        mock_service.name = f"execution-{execution_id}"

        def mock_services_list(filters):
            if filters.get("name") == f"execution-{execution_id}":
                return [mock_service]
            return []

        mock_client.services.list.side_effect = mock_services_list

        # Run the monitoring task
        with app.app_context():
            result = monitor_completed_docker_services.apply().result

        assert result["checked"] == 1
        assert result["completed_services_found"] == 1
        assert result["services_removed"] == 1

        # Verify execution status didn't change (already failed)
        with app.app_context():
            updated_execution = Execution.query.get(execution_id)
            assert updated_execution.status == "FAILED"

        # Verify Docker service was removed
        mock_service.remove.assert_called_once() @ patch(
            "gefapi.tasks.docker_completed_monitoring.get_docker_client"
        )

    def test_monitor_completed_docker_services_with_no_lingering_service(
        self, mock_get_docker_client, app, db_session, sample_execution
    ):
        """Test monitoring task when completed execution has no lingering service (good case)"""
        execution_id = sample_execution.id

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

        # Mock Docker client with no lingering services (good - already cleaned up)
        mock_client = MagicMock()
        mock_get_docker_client.return_value = mock_client
        mock_client.services.list.return_value = []

        # Run the monitoring task
        with app.app_context():
            result = monitor_completed_docker_services.apply().result

        assert result["checked"] >= 1
        assert result["completed_services_found"] == 0
        assert result["services_removed"] == 0

        # Verify execution status didn't change
        updated_execution = Execution.query.get(execution_id)
        assert updated_execution.status == "FINISHED"

    @patch("gefapi.tasks.docker_completed_monitoring.get_docker_client")
    def test_monitor_completed_docker_services_ignores_running_executions(
        self, mock_get_docker_client, app, db_session, sample_execution
    ):
        """Test that monitoring ignores executions that are still running"""
        # Set execution to RUNNING status (should be ignored)
        sample_execution.status = "RUNNING"
        sample_execution.start_date = datetime.datetime.utcnow() - datetime.timedelta(
            hours=1
        )
        db_session.add(sample_execution)
        db_session.commit()

        # Clear any other completed executions from other tests
        db_session.query(Execution).filter(
            Execution.status.in_(["FAILED", "FINISHED"])
        ).delete()
        db_session.commit()

        # Mock Docker client
        mock_client = MagicMock()
        mock_get_docker_client.return_value = mock_client

        # Run the monitoring task
        with app.app_context():
            result = monitor_completed_docker_services.apply().result

        # Should not check running executions
        assert result["checked"] == 0
        assert result["completed_services_found"] == 0
        assert result["services_removed"] == 0

        # Docker client should not be called for running executions
        mock_client.services.list.assert_not_called() @ patch(
            "gefapi.tasks.docker_completed_monitoring.get_docker_client"
        )

    def test_monitor_completed_docker_services_with_docker_unavailable(
        self, mock_get_docker_client, app, db_session, sample_execution
    ):
        """Test monitoring task when Docker is not available"""
        # Mock Docker client as unavailable
        mock_get_docker_client.return_value = None

        # Run the monitoring task
        with app.app_context():
            result = monitor_completed_docker_services.apply().result

        assert result["checked"] == 0
        assert result["completed_services_found"] == 0
        assert result["services_removed"] == 0
        assert result["error"] == "Docker unavailable"

    @patch("gefapi.tasks.docker_completed_monitoring.get_docker_client")
    def test_monitor_completed_docker_services_with_service_removal_error(
        self, mock_get_docker_client, app, db_session, sample_execution
    ):
        """Test monitoring task when service removal fails"""
        execution_id = sample_execution.id

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

        # Mock a lingering Docker service that fails to remove
        mock_service = MagicMock()
        mock_service.name = f"execution-{execution_id}"
        mock_service.remove.side_effect = Exception("Docker remove error")
        mock_client.services.list.return_value = [mock_service]

        # Run the monitoring task
        with app.app_context():
            result = monitor_completed_docker_services.apply().result

        assert result["checked"] >= 1
        assert result["completed_services_found"] == 1
        assert result["services_removed"] == 0  # Failed to remove

        # Verify execution status didn't change
        updated_execution = Execution.query.get(execution_id)
        assert updated_execution.status == "FINISHED"

        # Verify removal was attempted
        mock_service.remove.assert_called_once()

    def test_monitor_completed_docker_services_only_checks_recent_executions(
        self, app, db_session
    ):
        """Test that monitoring only checks recent executions"""
        # Create an old completed execution that should be ignored
        old_execution = Execution(
            status="FINISHED",
            start_date=datetime.datetime.utcnow() - datetime.timedelta(days=3),
            end_date=datetime.datetime.utcnow() - datetime.timedelta(days=2),
            progress=100,
        )
        db_session.add(old_execution)
        db_session.commit()

        # Mock Docker client
        with patch(
            "gefapi.tasks.docker_completed_monitoring.get_docker_client"
        ) as mock_get_docker_client:
            mock_client = MagicMock()
            mock_get_docker_client.return_value = mock_client
            mock_client.services.list.return_value = []

            # Run the monitoring task
            with app.app_context():
                result = monitor_completed_docker_services.apply().result

            # Should not check the old execution (older than 48 hours)
            assert result["checked"] == 0

    @patch("gefapi.tasks.docker_completed_monitoring.get_docker_client")
    def test_monitor_completed_docker_services_multiple_lingering_services(
        self, mock_get_docker_client, app, db_session
    ):
        """Test monitoring task with multiple executions having lingering services"""
        # Create multiple completed executions
        finished_execution = Execution(
            status="FINISHED",
            start_date=datetime.datetime.utcnow() - datetime.timedelta(hours=2),
            end_date=datetime.datetime.utcnow() - datetime.timedelta(hours=1),
            progress=100,
        )
        failed_execution = Execution(
            status="FAILED",
            start_date=datetime.datetime.utcnow() - datetime.timedelta(hours=3),
            end_date=datetime.datetime.utcnow() - datetime.timedelta(hours=2),
            progress=50,
        )
        db_session.add(finished_execution)
        db_session.add(failed_execution)
        db_session.commit()

        # Mock Docker client and services
        mock_client = MagicMock()
        mock_get_docker_client.return_value = mock_client

        # Mock lingering services for both executions
        mock_service_1 = MagicMock()
        mock_service_1.name = f"execution-{finished_execution.id}"
        mock_service_2 = MagicMock()
        mock_service_2.name = f"execution-{failed_execution.id}"

        # Set up the services.list to return appropriate service for each call
        def mock_services_list(filters):
            service_name = filters["name"]
            if service_name == f"execution-{finished_execution.id}":
                return [mock_service_1]
            elif service_name == f"execution-{failed_execution.id}":
                return [mock_service_2]
            return []

        mock_client.services.list.side_effect = mock_services_list

        # Run the monitoring task
        with app.app_context():
            result = monitor_completed_docker_services.apply().result

        assert result["checked"] == 2
        assert result["completed_services_found"] == 2
        assert result["services_removed"] == 2

        # Verify both services were removed
        mock_service_1.remove.assert_called_once()
        mock_service_2.remove.assert_called_once()
