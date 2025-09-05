"""
Tests for improved status tracking functionality.

Tests the new event-driven status tracking system that logs to status_log
whenever execution status changes.
"""

from unittest.mock import patch

import pytest

from gefapi import db
from gefapi.models import Execution, Script, StatusLog, User
from gefapi.services.execution_service import (
    ExecutionService,
    update_execution_status_with_logging,
)


@pytest.mark.usefixtures("client", "auth_headers_admin")
class TestImprovedStatusTracking:
    """Test improved status tracking functionality"""

    def test_status_log_model_new_schema(self, app):
        """Test that StatusLog model has the correct new schema"""
        with app.app_context():
            # Create a status log with new schema including transition fields
            status_log = StatusLog(
                executions_pending=2,
                executions_ready=2,
                executions_running=3,
                executions_finished=10,
                executions_failed=1,
                executions_cancelled=2,
                status_from="PENDING",
                status_to="RUNNING",
                execution_id="test-execution-id",
            )

            db.session.add(status_log)
            db.session.commit()

            # Test serialization includes new field and excludes old fields
            serialized = status_log.serialize()

            assert "executions_cancelled" in serialized
            assert serialized["executions_cancelled"] == 2

            # Test new transition fields
            assert "status_from" in serialized
            assert serialized["status_from"] == "PENDING"
            assert "status_to" in serialized
            assert serialized["status_to"] == "RUNNING"
            assert "execution_id" in serialized
            assert serialized["execution_id"] == "test-execution-id"

            # These fields should be removed
            assert "executions_count" not in serialized
            assert "users_count" not in serialized
            assert "scripts_count" not in serialized

            # Verify all expected fields are present
            expected_fields = {
                "id",
                "timestamp",
                "executions_pending",
                "executions_ready",
                "executions_running",
                "executions_finished",
                "executions_failed",
                "executions_cancelled",
                "status_from",
                "status_to",
                "execution_id",
            }
            assert set(serialized.keys()) == expected_fields

    def test_update_execution_status_with_logging_creates_status_log(self, app):
        """Test that updating execution status creates a status log entry"""
        import uuid

        with app.app_context():
            # Create test user with unique email
            user_uuid = uuid.uuid4()
            user = User(
                email=f"test1-{user_uuid}@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)

            # Create test script with unique slug
            script_uuid = uuid.uuid4()
            script = Script(
                name="Test Script",
                slug=f"test-script-1-{script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)

            # Create test execution
            execution = Execution(
                script_id=script.id, params={"test": "param"}, user_id=user.id
            )
            execution.status = "PENDING"
            db.session.add(execution)
            db.session.commit()

            # Get initial status log count
            initial_count = StatusLog.query.count()

            # Update execution status using the helper function
            status_log = update_execution_status_with_logging(execution, "RUNNING")

            # Verify one new status log was created (after change only)
            assert StatusLog.query.count() == initial_count + 1
            assert status_log is not None
            assert execution.status == "RUNNING"

            # Verify the status log has transition information
            assert status_log.status_from == "PENDING"
            assert status_log.status_to == "RUNNING"
            assert status_log.execution_id == str(execution.id)

    def test_update_execution_status_with_logging_counts_executions(self, app):
        """Test that status log contains correct execution counts"""
        import uuid

        with app.app_context():
            # Clean up all data using TRUNCATE CASCADE to avoid foreign key issues
            # This will reset the database to a clean state for this test
            try:
                db.session.execute(db.text("TRUNCATE TABLE status_log CASCADE"))
                db.session.execute(db.text("TRUNCATE TABLE execution_log CASCADE"))
                db.session.execute(db.text("TRUNCATE TABLE execution CASCADE"))
                db.session.execute(db.text("TRUNCATE TABLE script CASCADE"))
                db.session.execute(db.text('TRUNCATE TABLE "user" CASCADE'))
                db.session.execute(db.text("TRUNCATE TABLE refresh_tokens CASCADE"))
                db.session.commit()
            except Exception:
                # If TRUNCATE fails (e.g., SQLite in tests), fall back to individual deletes
                db.session.rollback()
                pass

            # Create test user
            user = User(
                email=f"test2-{uuid.uuid4()}@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)

            # Create test script
            script_uuid = uuid.uuid4()
            script = Script(
                name="Test Script",
                slug=f"test-script-2-{script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)

            # Create multiple executions with different statuses
            executions = []
            statuses = [
                "PENDING",
                "READY",
                "RUNNING",
                "FINISHED",
                "FAILED",
                "CANCELLED",
            ]
            for i, status in enumerate(statuses):
                execution = Execution(
                    script_id=script.id, params={"test": f"param{i}"}, user_id=user.id
                )
                execution.status = status
                executions.append(execution)
                db.session.add(execution)

            db.session.commit()

            # Create a new execution and update its status
            new_execution = Execution(
                script_id=script.id, params={"test": "new_param"}, user_id=user.id
            )
            new_execution.status = "PENDING"
            db.session.add(new_execution)
            db.session.commit()

            # Update to RUNNING status
            status_log = update_execution_status_with_logging(new_execution, "RUNNING")

            # Verify counts in status log (the one showing state after change)
            assert status_log.executions_ready == 1  # Only the original READY
            assert (
                status_log.executions_running == 2
            )  # RUNNING + PENDING (original) + new RUNNING
            assert status_log.executions_finished == 1  # Only the original FINISHED
            assert status_log.executions_failed == 1  # Only the original FAILED
            assert status_log.executions_cancelled == 1  # Only the original CANCELLED
            assert status_log.executions_pending == 1  # Only the original PENDING

            # Verify transition information
            assert status_log.status_from == "PENDING"
            assert status_log.status_to == "RUNNING"
            assert status_log.execution_id == str(new_execution.id)

    @patch("gefapi.services.execution_service.EmailService.send_html_email")
    def test_execution_service_update_uses_new_helper(self, mock_email, app):
        """Test that ExecutionService.update_execution uses the new helper function"""
        import uuid

        with app.app_context():
            # Create test user
            user = User(
                email=f"test3-{uuid.uuid4()}@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)
            db.session.commit()  # Commit user first to get user.id

            # Create test script
            script_uuid = uuid.uuid4()
            script = Script(
                name="Test Script",
                slug=f"test-script-3-{script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)
            db.session.commit()  # Commit script first to get script.id

            # Create test execution
            execution = Execution(
                script_id=script.id, params={"test": "param"}, user_id=user.id
            )
            execution.status = "RUNNING"
            db.session.add(execution)
            db.session.commit()

            execution_id = str(execution.id)
            initial_log_count = StatusLog.query.count()

            # Update execution to FINISHED
            updated_execution = ExecutionService.update_execution(
                {"status": "FINISHED"}, execution_id
            )

            # Verify status log was created
            assert (
                StatusLog.query.count() == initial_log_count + 1
            )  # One log per status change
            assert updated_execution.status == "FINISHED"
            assert updated_execution.end_date is not None
            assert updated_execution.progress == 100

    def test_execution_cancel_uses_new_helper(self, app):
        """Test that execution cancellation uses the new helper function"""
        import uuid

        with app.app_context():
            # Create test user with unique email
            user_uuid = uuid.uuid4()
            user = User(
                email=f"test4-{user_uuid}@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)

            # Create test script with unique slug
            script_uuid = uuid.uuid4()
            script = Script(
                name="Test Script",
                slug=f"test-script-4-{script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)

            # Create test execution
            execution = Execution(
                script_id=script.id, params={"test": "param"}, user_id=user.id
            )
            execution.status = "RUNNING"
            db.session.add(execution)
            db.session.commit()

            execution_id = str(execution.id)
            initial_log_count = StatusLog.query.count()

            # Mock celery task to avoid Docker dependencies in tests
            with patch("gefapi.services.execution_service.celery_app") as mock_celery:
                mock_task = mock_celery.send_task.return_value
                mock_task.get.return_value = {
                    "docker_service_stopped": True,
                    "docker_container_stopped": True,
                    "errors": [],
                }

                # Cancel the execution
                result = ExecutionService.cancel_execution(execution_id)

                # Verify status log was created
                assert (
                    StatusLog.query.count() == initial_log_count + 1
                )  # One log per status change
                assert result["execution"]["status"] == "CANCELLED"

    def test_status_endpoint_with_new_schema(self, client, auth_headers_admin):
        """Test that the status endpoint works with the new schema"""
        # Create a status log entry with new transition fields
        with client.application.app_context():
            status_log = StatusLog(
                executions_pending=2,
                executions_ready=2,
                executions_running=3,
                executions_finished=10,
                executions_failed=1,
                executions_cancelled=2,
                status_from="PENDING",
                status_to="RUNNING",
                execution_id="test-execution-id",
            )
            db.session.add(status_log)
            db.session.commit()

        # Call the status endpoint
        response = client.get("/api/v1/status", headers=auth_headers_admin)

        assert response.status_code == 200
        data = response.json

        assert "data" in data
        assert len(data["data"]) > 0

        status_entry = data["data"][0]

        # Verify new schema fields are present
        assert "executions_cancelled" in status_entry
        assert status_entry["executions_cancelled"] == 2
        assert "status_from" in status_entry
        assert "status_to" in status_entry
        assert "execution_id" in status_entry

        # Verify old fields are not present
        assert "executions_count" not in status_entry
        assert "users_count" not in status_entry
        assert "scripts_count" not in status_entry

    def test_helper_function_handles_terminal_states(self, app):
        """Test that the helper function properly handles terminal execution states"""
        import uuid

        with app.app_context():
            # Create test user with unique email
            user_uuid = uuid.uuid4()
            user = User(
                email=f"test5-{user_uuid}@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)

            # Create test script with unique slug
            script_uuid = uuid.uuid4()
            script = Script(
                name="Test Script",
                slug=f"test-script-5-{script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)

            # Create test execution
            execution = Execution(
                script_id=script.id, params={"test": "param"}, user_id=user.id
            )
            execution.status = "RUNNING"
            db.session.add(execution)
            db.session.commit()

            # Test each terminal state
            for terminal_status in ["FINISHED", "FAILED", "CANCELLED"]:
                # Update to terminal status
                status_log = update_execution_status_with_logging(
                    execution, terminal_status
                )

                # Verify execution is properly updated
                assert execution.status == terminal_status
                assert execution.end_date is not None
                assert execution.progress == 100

                # Verify status log reflects the change
                assert status_log is not None
                assert status_log.status_from == "RUNNING"
                assert status_log.status_to == terminal_status
                assert status_log.execution_id == str(execution.id)

                # Reset for next iteration (commit changes first)
                db.session.commit()
                execution.status = "RUNNING"
                execution.end_date = None
                execution.progress = 50
                db.session.commit()

    def test_helper_function_error_handling(self, app):
        """Test that the helper function handles database errors properly"""
        import uuid

        with app.app_context():
            # Create test user with unique email
            user_uuid = uuid.uuid4()
            user = User(
                email=f"test6-{user_uuid}@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)

            # Create test script with unique slug
            script_uuid = uuid.uuid4()
            script = Script(
                name="Test Script",
                slug=f"test-script-6-{script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)

            # Create test execution
            execution = Execution(
                script_id=script.id, params={"test": "param"}, user_id=user.id
            )
            execution.status = "RUNNING"
            db.session.add(execution)
            db.session.commit()

            # Simulate a database error by closing the session
            db.session.close()
            try:
                update_execution_status_with_logging(execution, "FAILED")
            except Exception as e:
                assert "closed" in str(e) or "Session" in str(e)
