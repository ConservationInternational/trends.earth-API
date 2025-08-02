"""Tests for execution cleanup tasks"""

import datetime
from unittest.mock import MagicMock, patch

from gefapi import db
from gefapi.models import Execution, ExecutionLog
from gefapi.tasks.execution_cleanup import (
    cleanup_finished_executions,
    cleanup_old_failed_executions,
    cleanup_stale_executions,
)


class TestExecutionCleanup:
    """Test execution cleanup functionality"""

    def test_cleanup_stale_executions_with_no_stale_executions(self, app, db_session):
        """Test cleanup when no stale executions exist"""
        with app.app_context():
            result = cleanup_stale_executions.apply().result

            assert result["cleaned_up"] == 0
            assert result["docker_services_removed"] == 0

    def test_cleanup_stale_executions_with_stale_pending_execution(
        self, app, db_session, sample_execution
    ):
        """Test cleanup of a stale PENDING execution"""
        with app.app_context():
            # Make the execution stale (started 4 days ago)
            four_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=4)
            sample_execution.start_date = four_days_ago
            sample_execution.status = "PENDING"
            sample_execution.end_date = None
            db_session.add(sample_execution)
            db_session.commit()

            execution_id = sample_execution.id

            # Mock Docker client to avoid actual Docker calls
            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client
                mock_client.services.list.return_value = []
                mock_client.containers.list.return_value = []

                result = cleanup_stale_executions.apply().result

                assert result["cleaned_up"] == 1
                assert result["docker_services_removed"] == 0

                # Verify execution was marked as FAILED
                db.session.commit()  # Ensure session is updated
                updated_execution = Execution.query.get(execution_id)
                db.session.refresh(updated_execution)  # Refresh from DB
                assert updated_execution.status == "FAILED"
                assert updated_execution.end_date is not None
                assert updated_execution.progress == 100

                # Verify Docker client was called to check for services/containers
                mock_client.services.list.assert_called_once_with(
                    filters={"name": f"execution-{execution_id}"}
                )
                mock_client.containers.list.assert_called_once_with(
                    filters={"name": f"execution-{execution_id}"}, all=True
                )

    def test_cleanup_stale_executions_with_running_execution_and_docker_service(
        self, app, sample_execution
    ):
        """Test cleanup of a stale RUNNING execution with Docker service"""
        with app.app_context():
            # Make the execution stale (started 5 days ago)
            five_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=5)
            sample_execution.start_date = five_days_ago
            sample_execution.status = "RUNNING"
            sample_execution.end_date = None
            db.session.add(sample_execution)
            db.session.commit()

            execution_id = sample_execution.id

            # Mock Docker client with a service to remove
            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client

                # Mock a Docker service that needs to be removed
                mock_service = MagicMock()
                mock_service.name = f"execution-{execution_id}"
                mock_client.services.list.return_value = [mock_service]
                mock_client.containers.list.return_value = []

                result = cleanup_stale_executions.apply().result

                assert result["cleaned_up"] == 1
                assert result["docker_services_removed"] == 1

                # Verify execution was marked as FAILED
                # Force a new database session to see committed changes
                db.session.close()
                with app.app_context():
                    updated_execution = (
                        db.session.query(Execution).filter_by(id=execution_id).first()
                    )
                    assert updated_execution.status == "FAILED"
                    assert updated_execution.end_date is not None

                # Verify Docker service was removed
                mock_service.remove.assert_called_once()

    def test_cleanup_stale_executions_ignores_finished_executions(
        self, app, db_session, sample_execution
    ):
        """Test that finished executions are not touched"""
        with app.app_context():
            # Make the execution old but FINISHED
            five_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=5)
            sample_execution.start_date = five_days_ago
            sample_execution.status = "FINISHED"
            sample_execution.end_date = datetime.datetime.utcnow()
            db_session.add(sample_execution)
            db_session.commit()

            original_status = sample_execution.status
            original_end_date = sample_execution.end_date

            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client

                result = cleanup_stale_executions.apply().result

                assert result["cleaned_up"] == 0
                assert result["docker_services_removed"] == 0

                # Verify execution was not modified
                updated_execution = Execution.query.get(sample_execution.id)
                assert updated_execution.status == original_status
                assert updated_execution.end_date == original_end_date

    def test_cleanup_stale_executions_ignores_failed_executions(
        self, app, db_session, sample_execution
    ):
        """Test that failed executions are not touched"""
        with app.app_context():
            # Make the execution old but FAILED
            five_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=5)
            sample_execution.start_date = five_days_ago
            sample_execution.status = "FAILED"
            sample_execution.end_date = datetime.datetime.utcnow()
            db_session.add(sample_execution)
            db_session.commit()

            original_status = sample_execution.status
            original_end_date = sample_execution.end_date

            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client

                result = cleanup_stale_executions.apply().result

                assert result["cleaned_up"] == 0
                assert result["docker_services_removed"] == 0

                # Verify execution was not modified
                updated_execution = Execution.query.get(sample_execution.id)
                assert updated_execution.status == original_status
                assert updated_execution.end_date == original_end_date

    def test_cleanup_stale_executions_ignores_recent_executions(
        self, app, db_session, sample_execution
    ):
        """Test that recent executions are not touched"""
        with app.app_context():
            # Make the execution recent (started 1 day ago)
            one_day_ago = datetime.datetime.utcnow() - datetime.timedelta(days=1)
            sample_execution.start_date = one_day_ago
            sample_execution.status = "RUNNING"
            sample_execution.end_date = None
            db_session.add(sample_execution)
            db_session.commit()

            original_status = sample_execution.status

            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client

                result = cleanup_stale_executions.apply().result

                assert result["cleaned_up"] == 0
                assert result["docker_services_removed"] == 0

                # Verify execution was not modified
                updated_execution = Execution.query.get(sample_execution.id)
                assert updated_execution.status == original_status

    def test_cleanup_stale_executions_with_docker_unavailable(
        self, app, sample_execution
    ):
        """Test cleanup when Docker is not available"""
        with app.app_context():
            # Make the execution stale
            four_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=4)
            sample_execution.start_date = four_days_ago
            sample_execution.status = "PENDING"
            sample_execution.end_date = None
            db.session.add(sample_execution)
            db.session.commit()

            execution_id = sample_execution.id

            # Mock Docker client to return None (unavailable)
            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_docker.return_value = None

                result = cleanup_stale_executions.apply().result

                assert result["cleaned_up"] == 1
                assert result["docker_services_removed"] == 0

                # Force a new database session to see committed changes
                db.session.close()
                with app.app_context():
                    updated_execution = (
                        db.session.query(Execution).filter_by(id=execution_id).first()
                    )
                    assert updated_execution.status == "FAILED"

    def test_cleanup_stale_executions_with_docker_error(self, app, sample_execution):
        """Test cleanup continues when Docker operations fail"""
        with app.app_context():
            # Make the execution stale
            four_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=4)
            sample_execution.start_date = four_days_ago
            sample_execution.status = "RUNNING"
            sample_execution.end_date = None
            db.session.add(sample_execution)
            db.session.commit()

            execution_id = sample_execution.id

            # Mock Docker client to raise an exception
            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client
                mock_client.services.list.side_effect = Exception("Docker error")

                result = cleanup_stale_executions.apply().result

                assert result["cleaned_up"] == 1
                # Docker removal failed but execution was still cleaned up
                assert result["docker_services_removed"] == 0

                # Verify execution was still marked as FAILED
                # Force a new database session to see committed changes
                db.session.close()
                with app.app_context():
                    updated_execution = (
                        db.session.query(Execution).filter_by(id=execution_id).first()
                    )
                    assert updated_execution.status == "FAILED"

    def test_cleanup_finished_executions_with_recent_finished_execution(
        self, app, db_session, sample_execution
    ):
        """Test cleanup of recently finished executions with Docker service"""
        with app.app_context():
            # Clean up any existing finished executions to ensure test isolation
            existing_finished_executions = (
                db_session.query(Execution).filter_by(status="FINISHED").all()
            )
            for exec in existing_finished_executions:
                db_session.delete(exec)
            db_session.commit()

            # Make the execution finished recently (2 hours ago)
            two_hours_ago = datetime.datetime.utcnow() - datetime.timedelta(hours=2)
            sample_execution.start_date = two_hours_ago - datetime.timedelta(hours=1)
            sample_execution.end_date = two_hours_ago
            sample_execution.status = "FINISHED"
            db_session.add(sample_execution)
            db_session.commit()

            execution_id = sample_execution.id

            # Mock Docker client with a service to remove
            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client

                # Mock a Docker service that needs to be removed
                mock_service = MagicMock()
                mock_service.name = f"execution-{execution_id}"
                mock_client.services.list.return_value = [mock_service]
                mock_client.containers.list.return_value = []

                result = cleanup_finished_executions.apply().result

                assert result["cleaned_up"] == 1
                assert result["docker_services_removed"] == 1

                # Verify execution status wasn't changed (it's already FINISHED)
                updated_execution = Execution.query.get(execution_id)
                assert updated_execution.status == "FINISHED"

                # Verify Docker service was removed
                mock_service.remove.assert_called_once()

    def test_cleanup_finished_executions_ignores_old_finished_executions(
        self, app, db_session, regular_user, sample_script
    ):
        """Test that finished executions older than 1 day are ignored"""
        with app.app_context():
            # Clean up any existing finished executions to avoid interference
            # First, get all finished executions and delete them properly with cascade
            finished_executions = (
                db_session.query(Execution).filter_by(status="FINISHED").all()
            )
            for execution in finished_executions:
                # Delete related execution logs first
                db_session.query(ExecutionLog).filter_by(
                    execution_id=execution.id
                ).delete()
                # Then delete the execution
                db_session.delete(execution)
            db_session.commit()

            # Create a new execution for this test to avoid interference
            two_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=2)

            # Merge the objects to ensure they're attached to the current session
            script = db_session.merge(sample_script)
            user = db_session.merge(regular_user)

            # Create a unique execution for this test
            execution = Execution(
                script_id=script.id,
                user_id=user.id,
                params={"test_param": "old_execution"},
            )
            execution.start_date = two_days_ago - datetime.timedelta(hours=1)
            execution.end_date = two_days_ago
            execution.status = "FINISHED"
            db_session.add(execution)
            db_session.commit()

            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client

                result = cleanup_finished_executions.apply().result

                assert result["cleaned_up"] == 0
                assert result["docker_services_removed"] == 0

    def test_cleanup_finished_executions_ignores_running_executions(
        self, app, db_session, sample_execution
    ):
        """Test that running executions are ignored by finished cleanup"""
        with app.app_context():
            # Make the execution running (started recently)
            one_hour_ago = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
            sample_execution.start_date = one_hour_ago
            sample_execution.end_date = None
            sample_execution.status = "RUNNING"
            db_session.add(sample_execution)
            db_session.commit()

            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client

                result = cleanup_finished_executions.apply().result

                assert result["cleaned_up"] == 0
                assert result["docker_services_removed"] == 0

    def test_cleanup_finished_executions_with_docker_unavailable(
        self, app, db_session, sample_execution
    ):
        """Test finished cleanup when Docker is not available"""
        with app.app_context():
            # Make the execution finished recently
            one_hour_ago = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
            sample_execution.start_date = one_hour_ago - datetime.timedelta(hours=1)
            sample_execution.end_date = one_hour_ago
            sample_execution.status = "FINISHED"
            db_session.add(sample_execution)
            db_session.commit()

            execution_id = sample_execution.id

            # Mock Docker client to return None (unavailable)
            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_docker.return_value = None

                from gefapi.tasks.execution_cleanup import cleanup_finished_executions

                result = cleanup_finished_executions.apply().result

                assert result["cleaned_up"] == 1
                assert result["docker_services_removed"] == 0

                # Verify execution status wasn't changed
                updated_execution = Execution.query.get(execution_id)
                assert updated_execution.status == "FINISHED"

    def test_cleanup_old_failed_executions_with_old_failed_execution(
        self, app, db_session, sample_execution
    ):
        """Test cleanup of old failed executions with Docker service"""
        with app.app_context():
            # Make the execution failed 15 days ago
            fifteen_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=15)
            sample_execution.start_date = fifteen_days_ago - datetime.timedelta(hours=1)
            sample_execution.end_date = fifteen_days_ago
            sample_execution.status = "FAILED"
            db_session.add(sample_execution)
            db_session.commit()

            execution_id = sample_execution.id

            # Mock Docker client with a service to remove
            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client

                # Mock a Docker service that needs to be removed
                mock_service = MagicMock()
                mock_service.name = f"execution-{execution_id}"
                mock_client.services.list.return_value = [mock_service]
                mock_client.containers.list.return_value = []

                result = cleanup_old_failed_executions.apply().result

                # We expect to clean up at least 1 execution (the one we just created)
                # but there might be others from previous tests, so use >=
                assert result["cleaned_up"] >= 1
                assert result["docker_services_removed"] >= 1

                # Verify that our specific execution was cleaned up
                # (it should still exist but Docker service should have been removed)
                updated_execution = Execution.query.get(execution_id)
                assert updated_execution.status == "FAILED"

                # Verify Docker service was removed (at least once for any execution)
                assert mock_service.remove.call_count >= 1

    def test_cleanup_old_failed_executions_ignores_recent_failed_executions(
        self, app, db_session, sample_execution
    ):
        """Test that failed executions newer than 14 days are ignored"""
        with app.app_context():
            # First, clean up any existing old failed executions to ensure test isolation
            # But exclude the current sample_execution to avoid StaleDataError
            cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(days=14)
            old_failed_executions = Execution.query.filter(
                Execution.status == "FAILED",
                Execution.end_date.isnot(None),
                Execution.end_date < cutoff_date,
                Execution.id != sample_execution.id,
            ).all()

            for old_execution in old_failed_executions:
                db_session.delete(old_execution)
            db_session.commit()

            # Make the execution failed 10 days ago (newer than 14 days)
            ten_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=10)
            sample_execution.start_date = ten_days_ago - datetime.timedelta(hours=1)
            sample_execution.end_date = ten_days_ago
            sample_execution.status = "FAILED"
            db_session.add(sample_execution)
            db_session.commit()

            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client

                result = cleanup_old_failed_executions.apply().result

                assert result["cleaned_up"] == 0
                assert result["docker_services_removed"] == 0

    def test_cleanup_old_failed_executions_ignores_successful_executions(
        self, app, db_session, sample_execution
    ):
        """Test that successful executions are ignored by failed cleanup"""
        with app.app_context():
            # Make the execution finished 20 days ago
            twenty_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=20)
            sample_execution.start_date = twenty_days_ago - datetime.timedelta(hours=1)
            sample_execution.end_date = twenty_days_ago
            sample_execution.status = "FINISHED"
            db_session.add(sample_execution)
            db_session.commit()

            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client

                result = cleanup_old_failed_executions.apply().result

                assert result["cleaned_up"] == 0
                assert result["docker_services_removed"] == 0

    def test_cleanup_old_failed_executions_with_docker_unavailable(
        self, app, db_session, sample_execution
    ):
        """Test old failed cleanup when Docker is not available"""
        with app.app_context():
            # First, clean up any existing old failed executions to ensure test isolation
            # But exclude the current sample_execution to avoid StaleDataError
            cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(days=14)
            old_failed_executions = Execution.query.filter(
                Execution.status == "FAILED",
                Execution.end_date.isnot(None),
                Execution.end_date < cutoff_date,
                Execution.id != sample_execution.id,
            ).all()

            for old_execution in old_failed_executions:
                db_session.delete(old_execution)
            db_session.commit()

            # Make the execution failed 20 days ago
            twenty_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=20)
            sample_execution.start_date = twenty_days_ago - datetime.timedelta(hours=1)
            sample_execution.end_date = twenty_days_ago
            sample_execution.status = "FAILED"
            db_session.add(sample_execution)
            db_session.commit()

            execution_id = sample_execution.id

            # Mock Docker client to return None (unavailable)
            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_docker.return_value = None

                result = cleanup_old_failed_executions.apply().result

                assert result["cleaned_up"] == 1
                assert result["docker_services_removed"] == 0

                # Verify execution status wasn't changed
                updated_execution = Execution.query.get(execution_id)
                assert updated_execution.status == "FAILED"

    def test_cleanup_old_failed_executions_with_containers_and_services(
        self, app, db_session, sample_execution
    ):
        """Test cleanup of old failed executions with both Docker services and containers"""
        with app.app_context():
            # First, clean up any existing old failed executions to ensure test isolation
            # But exclude the current sample_execution to avoid StaleDataError
            cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(days=14)
            old_failed_executions = Execution.query.filter(
                Execution.status == "FAILED",
                Execution.end_date.isnot(None),
                Execution.end_date < cutoff_date,
                Execution.id != sample_execution.id,
            ).all()

            for old_execution in old_failed_executions:
                db_session.delete(old_execution)
            db_session.commit()

            # Make the execution failed 30 days ago
            thirty_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=30)
            sample_execution.start_date = thirty_days_ago - datetime.timedelta(hours=1)
            sample_execution.end_date = thirty_days_ago
            sample_execution.status = "FAILED"
            db_session.add(sample_execution)
            db_session.commit()

            execution_id = sample_execution.id

            # Mock Docker client with both services and containers to remove
            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client

                # Mock a Docker service and container that need to be removed
                mock_service = MagicMock()
                mock_service.name = f"execution-{execution_id}"
                mock_container = MagicMock()
                mock_container.name = f"execution-{execution_id}"

                mock_client.services.list.return_value = [mock_service]
                mock_client.containers.list.return_value = [mock_container]

                result = cleanup_old_failed_executions.apply().result

                assert result["cleaned_up"] == 1
                assert result["docker_services_removed"] == 2  # 1 service + 1 container

                # Verify execution status wasn't changed (it's already FAILED)
                updated_execution = Execution.query.get(execution_id)
                assert updated_execution.status == "FAILED"

                # Verify Docker service and container were removed
                mock_service.remove.assert_called_once()
                mock_container.remove.assert_called_once_with(force=True)

    def test_cleanup_old_failed_executions_with_docker_error(
        self, app, db_session, sample_execution
    ):
        """Test cleanup continues when Docker operations fail for old failed executions"""
        with app.app_context():
            # First, clean up any existing old failed executions to ensure test isolation
            # But exclude the current sample_execution to avoid StaleDataError
            cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(days=14)
            old_failed_executions = Execution.query.filter(
                Execution.status == "FAILED",
                Execution.end_date.isnot(None),
                Execution.end_date < cutoff_date,
                Execution.id != sample_execution.id,
            ).all()

            for old_execution in old_failed_executions:
                db_session.delete(old_execution)
            db_session.commit()

            # Make the execution failed 20 days ago
            twenty_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=20)
            sample_execution.start_date = twenty_days_ago - datetime.timedelta(hours=1)
            sample_execution.end_date = twenty_days_ago
            sample_execution.status = "FAILED"
            db_session.add(sample_execution)
            db_session.commit()

            execution_id = sample_execution.id

            # Mock Docker client to raise an exception
            with patch(
                "gefapi.tasks.execution_cleanup.get_docker_client"
            ) as mock_docker:
                mock_client = MagicMock()
                mock_docker.return_value = mock_client
                mock_client.services.list.side_effect = Exception("Docker error")

                result = cleanup_old_failed_executions.apply().result

                assert result["cleaned_up"] == 1
                # Docker removal failed but execution was still processed
                assert result["docker_services_removed"] == 0

                # Verify execution status wasn't changed (it's already FAILED)
                updated_execution = Execution.query.get(execution_id)
                assert updated_execution.status == "FAILED"
