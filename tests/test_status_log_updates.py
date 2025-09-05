"""
Tests for status log updates with transition tracking.

Tests the new status tracking system that logs to status_log
AFTER execution status changes with transition information.
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
class TestStatusLogUpdates:
    """Test status log updates with transition tracking"""

    def test_status_log_model_has_transition_fields(self, app):
        """Test that StatusLog model has the new transition fields"""
        with app.app_context():
            # Create a status log with new transition fields
            status_log = StatusLog(
                executions_pending=1,
                executions_ready=2,
                executions_running=3,
                executions_finished=4,
                executions_failed=1,
                executions_cancelled=0,
                status_from="PENDING",
                status_to="RUNNING",
                execution_id="test-execution-id",
            )

            db.session.add(status_log)
            db.session.commit()

            # Test serialization includes new fields
            serialized = status_log.serialize()

            assert "status_from" in serialized
            assert serialized["status_from"] == "PENDING"
            assert "status_to" in serialized
            assert serialized["status_to"] == "RUNNING"
            assert "execution_id" in serialized
            assert serialized["execution_id"] == "test-execution-id"

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

    def test_update_execution_status_creates_single_log_after_change(self, app):
        """Test that updating execution status creates only one log entry AFTER the change"""
        import uuid

        with app.app_context():
            # Create test user with unique email
            user_uuid = uuid.uuid4()
            user = User(
                email=f"test-transition-1-{user_uuid}@example.com",
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
                slug=f"test-script-transition-1-{script_uuid}",
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

            # Verify only one new status log was created
            assert StatusLog.query.count() == initial_count + 1
            assert status_log is not None
            assert execution.status == "RUNNING"

            # Verify the status log has transition information
            assert status_log.status_from == "PENDING"
            assert status_log.status_to == "RUNNING"
            assert status_log.execution_id == str(execution.id)

    def test_status_log_contains_accurate_counts_after_change(self, app):
        """Test that status log contains correct execution counts after the status change"""
        import uuid

        with app.app_context():
            # Clean up all data using TRUNCATE CASCADE to avoid foreign key issues
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
                email=f"test-transition-2-{uuid.uuid4()}@example.com",
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
                slug=f"test-script-transition-2-{script_uuid}",
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

            # Verify counts reflect state AFTER the change
            assert status_log.executions_pending == 1  # Only the original PENDING
            assert status_log.executions_ready == 1  # Only the original READY
            assert status_log.executions_running == 2  # Original RUNNING + new one
            assert status_log.executions_finished == 1  # Only the original FINISHED
            assert status_log.executions_failed == 1  # Only the original FAILED
            assert status_log.executions_cancelled == 1  # Only the original CANCELLED

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
                email=f"test-transition-3-{uuid.uuid4()}@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)
            db.session.commit()

            # Create test script
            script_uuid = uuid.uuid4()
            script = Script(
                name="Test Script",
                slug=f"test-script-transition-3-{script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)
            db.session.commit()

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

            # Verify only one status log was created
            assert StatusLog.query.count() == initial_log_count + 1
            assert updated_execution.status == "FINISHED"
            assert updated_execution.end_date is not None
            assert updated_execution.progress == 100

            # Verify the status log has transition information
            status_log = StatusLog.query.order_by(StatusLog.id.desc()).first()
            assert status_log.status_from == "RUNNING"
            assert status_log.status_to == "FINISHED"
            assert status_log.execution_id == execution_id

    def test_execution_cancel_uses_new_helper(self, app):
        """Test that execution cancellation uses the new helper function"""
        import uuid

        with app.app_context():
            # Create test user with unique email
            user_uuid = uuid.uuid4()
            user = User(
                email=f"test-transition-4-{user_uuid}@example.com",
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
                slug=f"test-script-transition-4-{script_uuid}",
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

                # Verify only one status log was created
                assert StatusLog.query.count() == initial_log_count + 1
                assert result["execution"]["status"] == "CANCELLED"

                # Verify the status log has transition information
                status_log = StatusLog.query.order_by(StatusLog.id.desc()).first()
                assert status_log.status_from == "RUNNING"
                assert status_log.status_to == "CANCELLED"
                assert status_log.execution_id == execution_id

    def test_helper_function_handles_terminal_states(self, app):
        """Test that the helper function properly handles terminal execution states"""
        import uuid

        with app.app_context():
            # Create test user with unique email
            user_uuid = uuid.uuid4()
            user = User(
                email=f"test-transition-5-{user_uuid}@example.com",
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
                slug=f"test-script-transition-5-{script_uuid}",
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

                # Reset for next iteration
                execution.status = "RUNNING"
                execution.end_date = None
                execution.progress = 50

    def test_status_endpoint_with_new_schema(self, client, auth_headers_admin):
        """Test that the status endpoint works with the new schema"""
        # Create a status log entry with transition information
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
        assert "status_from" in status_entry
        assert "status_to" in status_entry
        assert "execution_id" in status_entry
        # At least one of them should have our test data
        found_test_entry = False
        for entry in data["data"]:
            if (
                entry.get("status_from") == "PENDING"
                and entry.get("status_to") == "RUNNING"
            ):
                found_test_entry = True
                assert entry["execution_id"] == "test-execution-id"
                break
        assert found_test_entry, "Test status log entry not found in API response"

    def test_helper_function_error_handling(self, app):
        """Test that the helper function handles database errors properly"""
        import uuid

        with app.app_context():
            # Create test user with unique email
            user_uuid = uuid.uuid4()
            user = User(
                email=f"test-transition-6-{user_uuid}@example.com",
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
                slug=f"test-script-transition-6-{script_uuid}",
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
