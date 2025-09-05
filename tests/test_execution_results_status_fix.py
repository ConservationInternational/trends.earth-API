"""
Test for execution results and status update fix.

This test verifies the fix for the issue where execution results were not being
saved when status was also updated in the same request (commit 1447c9c bug).
"""

from unittest.mock import patch
import uuid

import pytest

from gefapi import db
from gefapi.models import Execution, Script, User
from gefapi.services.execution_service import ExecutionService


@pytest.mark.usefixtures("app")
class TestExecutionResultsAndStatusUpdate:
    """Test that executions can save results with or without status changes."""

    def setup_method(self):
        """Set up test data for each test method."""
        self.user_uuid = uuid.uuid4()
        self.script_uuid = uuid.uuid4()

    @patch("gefapi.services.execution_service.EmailService.send_html_email")
    def test_update_status_and_results_simultaneously(self, mock_email, app):
        """
        Test that updating status=FINISHED and results together works correctly.

        This was the main bug - when containers sent both status and results,
        only the status was saved but results remained empty {}.
        """
        with app.app_context():
            # Create test user
            user = User(
                email=f"test-results-status-{self.user_uuid}@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)
            db.session.commit()

            # Create test script
            script = Script(
                name="Test Script",
                slug=f"test-script-results-{self.script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)
            db.session.commit()

            # Create test execution
            execution = Execution(
                script_id=script.id,
                params={"task_name": "Test Task"},
                user_id=user.id,
            )
            execution.status = "RUNNING"
            execution.progress = 50
            execution.results = {}  # Initially empty
            db.session.add(execution)
            db.session.commit()

            execution_id = str(execution.id)

            # Simulate what a container would send when finishing:
            # Both status change AND results data
            update_data = {
                "status": "FINISHED",
                "results": {
                    "output": "success",
                    "files": ["output.tif", "metadata.json"],
                    "statistics": {"mean": 0.5, "std": 0.2},
                },
            }

            # Update execution
            updated_execution = ExecutionService.update_execution(
                update_data, execution_id
            )

            # Verify ALL fields were updated correctly
            assert updated_execution.status == "FINISHED"
            assert updated_execution.end_date is not None
            assert updated_execution.progress == 100  # Auto-set for terminal state

            # CRITICAL: Results should be saved, not remain empty
            assert updated_execution.results is not None
            assert updated_execution.results != {}
            assert updated_execution.results["output"] == "success"
            assert updated_execution.results["files"] == ["output.tif", "metadata.json"]
            assert updated_execution.results["statistics"]["mean"] == 0.5

            # Verify email notification was attempted
            mock_email.assert_called_once()

    @patch("gefapi.services.execution_service.EmailService.send_html_email")
    def test_update_status_and_results_with_explicit_progress(self, mock_email, app):
        """
        Test that explicit progress is preserved when updating status and results.
        """
        with app.app_context():
            # Create test user
            user = User(
                email=f"test-progress-{self.user_uuid}@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)
            db.session.commit()

            # Create test script
            script = Script(
                name="Test Script",
                slug=f"test-script-progress-{self.script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)
            db.session.commit()

            # Create test execution
            execution = Execution(
                script_id=script.id,
                params={"task_name": "Test Task"},
                user_id=user.id,
            )
            execution.status = "RUNNING"
            execution.progress = 75
            execution.results = {}
            db.session.add(execution)
            db.session.commit()

            execution_id = str(execution.id)

            # Container sends status, results, AND explicit progress
            update_data = {
                "status": "FINISHED",
                "progress": 95,  # Explicit progress (not 100)
                "results": {"final_output": "completed"},
            }

            # Update execution
            updated_execution = ExecutionService.update_execution(
                update_data, execution_id
            )

            # Verify explicit progress is preserved (not auto-set to 100)
            assert updated_execution.status == "FINISHED"
            assert updated_execution.progress == 95  # Should preserve explicit value
            assert updated_execution.results["final_output"] == "completed"

    def test_update_results_without_status_change(self, app):
        """
        Test that updating only results (without status) works correctly.

        This should work in both the old and new code.
        """
        with app.app_context():
            # Create test user
            user = User(
                email=f"test-results-only-{self.user_uuid}@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)
            db.session.commit()

            # Create test script
            script = Script(
                name="Test Script",
                slug=f"test-script-results-only-{self.script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)
            db.session.commit()

            # Create test execution
            execution = Execution(
                script_id=script.id,
                params={"task_name": "Test Task"},
                user_id=user.id,
            )
            execution.status = "RUNNING"
            execution.progress = 80
            execution.results = {"intermediate": "data"}
            db.session.add(execution)
            db.session.commit()

            execution_id = str(execution.id)
            original_status = execution.status

            # Update only results (no status change)
            update_data = {
                "results": {
                    "intermediate": "data",
                    "new_output": "additional_results",
                }
            }

            # Update execution
            updated_execution = ExecutionService.update_execution(
                update_data, execution_id
            )

            # Verify results updated but status unchanged
            assert updated_execution.status == original_status  # No change
            assert updated_execution.progress == 80  # No change
            assert updated_execution.results["intermediate"] == "data"
            assert updated_execution.results["new_output"] == "additional_results"

    def test_update_progress_without_status_change(self, app):
        """
        Test that updating only progress (without status) works correctly.
        """
        with app.app_context():
            # Create test user
            user = User(
                email=f"test-progress-only-{self.user_uuid}@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)
            db.session.commit()

            # Create test script
            script = Script(
                name="Test Script",
                slug=f"test-script-progress-only-{self.script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)
            db.session.commit()

            # Create test execution
            execution = Execution(
                script_id=script.id,
                params={"task_name": "Test Task"},
                user_id=user.id,
            )
            execution.status = "RUNNING"
            execution.progress = 60
            execution.results = {"some": "data"}
            db.session.add(execution)
            db.session.commit()

            execution_id = str(execution.id)
            original_status = execution.status
            original_results = execution.results.copy()

            # Update only progress
            update_data = {"progress": 85}

            # Update execution
            updated_execution = ExecutionService.update_execution(
                update_data, execution_id
            )

            # Verify progress updated but other fields unchanged
            assert updated_execution.status == original_status
            assert updated_execution.progress == 85
            assert updated_execution.results == original_results

    @patch("gefapi.services.execution_service.EmailService.send_html_email")
    def test_multiple_status_and_results_updates(self, mock_email, app):
        """
        Test multiple sequential updates with different combinations.
        """
        with app.app_context():
            # Create test user
            user = User(
                email=f"test-multiple-{self.user_uuid}@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)
            db.session.commit()

            # Create test script
            script = Script(
                name="Test Script",
                slug=f"test-script-multiple-{self.script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)
            db.session.commit()

            # Create test execution
            execution = Execution(
                script_id=script.id,
                params={"task_name": "Test Task"},
                user_id=user.id,
            )
            execution.status = "PENDING"
            execution.progress = 0
            execution.results = {}
            db.session.add(execution)
            db.session.commit()

            execution_id = str(execution.id)

            # Update 1: Start running
            ExecutionService.update_execution({"status": "RUNNING"}, execution_id)

            updated = ExecutionService.get_execution(execution_id)
            assert updated.status == "RUNNING"

            # Update 2: Progress only
            ExecutionService.update_execution({"progress": 25}, execution_id)

            updated = ExecutionService.get_execution(execution_id)
            assert updated.progress == 25
            assert updated.status == "RUNNING"

            # Update 3: Intermediate results
            ExecutionService.update_execution(
                {"results": {"partial": "output"}}, execution_id
            )

            updated = ExecutionService.get_execution(execution_id)
            assert updated.results["partial"] == "output"
            assert updated.status == "RUNNING"

            # Update 4: Progress + results (no status)
            ExecutionService.update_execution(
                {
                    "progress": 75,
                    "results": {"partial": "output", "more": "data"},
                },
                execution_id,
            )

            updated = ExecutionService.get_execution(execution_id)
            assert updated.progress == 75
            assert updated.results["more"] == "data"
            assert updated.status == "RUNNING"

            # Update 5: Final - status + results + progress
            ExecutionService.update_execution(
                {
                    "status": "FINISHED",
                    "progress": 90,  # Explicit final progress
                    "results": {
                        "partial": "output",
                        "more": "data",
                        "final": "complete",
                    },
                },
                execution_id,
            )

            final = ExecutionService.get_execution(execution_id)
            assert final.status == "FINISHED"
            assert final.progress == 90  # Preserved explicit progress
            assert final.results["final"] == "complete"
            assert final.end_date is not None

            # Verify email was sent for terminal state
            mock_email.assert_called_once()
