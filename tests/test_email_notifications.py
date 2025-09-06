"""
Tests for email notification preferences functionality.

Tests the new email_notifications_enabled user preference and its impact on
execution completion emails.
"""

from unittest.mock import patch
import uuid

import pytest

from gefapi import db
from gefapi.models import Execution, Script, User
from gefapi.services.execution_service import ExecutionService


def generate_unique_email(prefix="test"):
    """Generate a unique email for testing"""
    unique_id = uuid.uuid4().hex[:8]
    return f"{prefix}-{unique_id}@example.com"


@pytest.mark.usefixtures("client", "auth_headers_admin")
class TestEmailNotificationPreferences:
    """Test email notification preferences functionality"""

    @patch("gefapi.services.execution_service.EmailService.send_html_email")
    def test_email_sent_when_notifications_enabled(self, mock_email, app):
        """Test that email is sent when user has notifications enabled (default)"""
        with app.app_context():
            # Create test user (default email_notifications_enabled=True)
            user = User(
                email=generate_unique_email("enabled"),
                password="password123",
                name="Test User Enabled",
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
                slug=f"test-script-email-{script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)
            db.session.commit()

            # Create test execution
            execution = Execution(
                script_id=script.id, params={"task_name": "Test Task"}, user_id=user.id
            )
            execution.status = "RUNNING"
            db.session.add(execution)
            db.session.commit()

            execution_id = str(execution.id)

            # Update execution to FINISHED
            ExecutionService.update_execution({"status": "FINISHED"}, execution_id)

            # Verify email was sent
            mock_email.assert_called_once()
            call_args = mock_email.call_args
            assert user.email in call_args[1]["recipients"]
            assert "Execution finished" in call_args[1]["subject"]

    @patch("gefapi.services.execution_service.EmailService.send_html_email")
    def test_email_not_sent_when_notifications_disabled(self, mock_email, app):
        """Test that email is not sent when user has notifications disabled"""
        with app.app_context():
            # Create test user with notifications disabled
            user = User(
                email=generate_unique_email("disabled"),
                password="password123",
                name="Test User Disabled",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            user.email_notifications_enabled = False
            db.session.add(user)
            db.session.commit()

            # Create test script
            script_uuid = uuid.uuid4()
            script = Script(
                name="Test Script",
                slug=f"test-script-no-email-{script_uuid}",
                user_id=user.id,
            )
            script.status = "SUCCESS"
            db.session.add(script)
            db.session.commit()

            # Create test execution
            execution = Execution(
                script_id=script.id, params={"task_name": "Test Task"}, user_id=user.id
            )
            execution.status = "RUNNING"
            db.session.add(execution)
            db.session.commit()

            execution_id = str(execution.id)

            # Update execution to FINISHED
            ExecutionService.update_execution({"status": "FINISHED"}, execution_id)

            # Verify email was NOT sent
            mock_email.assert_not_called()

    @patch("gefapi.services.execution_service.EmailService.send_html_email")
    def test_email_sent_for_all_terminal_states(self, mock_email, app):
        """Test that emails are sent for FINISHED, FAILED, and CANCELLED states"""
        with app.app_context():
            terminal_states = ["FINISHED", "FAILED", "CANCELLED"]

            for status in terminal_states:
                # Create test user with notifications enabled
                user = User(
                    email=generate_unique_email(f"terminal-{status.lower()}"),
                    password="password123",
                    name=f"Test User {status}",
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
                    slug=f"test-script-{status.lower()}-{script_uuid}",
                    user_id=user.id,
                )
                script.status = "SUCCESS"
                db.session.add(script)
                db.session.commit()

                # Create test execution
                execution = Execution(
                    script_id=script.id,
                    params={"task_name": f"Test Task {status}"},
                    user_id=user.id,
                )
                execution.status = "RUNNING"
                db.session.add(execution)
                db.session.commit()

                execution_id = str(execution.id)

                # Reset mock for each iteration
                mock_email.reset_mock()

                # Update execution to terminal state
                ExecutionService.update_execution({"status": status}, execution_id)

                # Verify email was sent for this terminal state
                mock_email.assert_called_once()
                call_args = mock_email.call_args
                assert user.email in call_args[1]["recipients"]
                assert "Execution finished" in call_args[1]["subject"]

    def test_user_model_includes_email_notifications_preference(self, app):
        """Test that user model includes email_notifications_enabled field"""
        with app.app_context():
            # Create test user
            user = User(
                email=generate_unique_email("serialize"),
                password="password123",
                name="Test User Serialize",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )

            # Verify default value
            assert user.email_notifications_enabled is True

            # Test changing the value
            user.email_notifications_enabled = False
            assert user.email_notifications_enabled is False

            # Test serialization includes the field
            serialized = user.serialize()
            assert "email_notifications_enabled" in serialized
            assert serialized["email_notifications_enabled"] is False

    def test_user_service_updates_email_notifications_preference(self, app):
        """Test that UserService.update_user can update email notification preferences"""
        from gefapi.services.user_service import UserService

        with app.app_context():
            # Create test user
            user = User(
                email=generate_unique_email("service-update"),
                password="password123",
                name="Test User Service",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )
            db.session.add(user)
            db.session.commit()

            user_id = str(user.id)

            # Verify default value
            assert user.email_notifications_enabled is True

            # Update using UserService
            updated_user = UserService.update_user(
                {"email_notifications_enabled": False}, user_id
            )

            # Verify the update worked
            assert updated_user.email_notifications_enabled is False

            # Verify persistence
            fresh_user = UserService.get_user(user_id)
            assert fresh_user.email_notifications_enabled is False

            # Test updating back to True
            updated_user = UserService.update_user(
                {"email_notifications_enabled": True}, user_id
            )
            assert updated_user.email_notifications_enabled is True

    def test_profile_update_api_handles_email_notifications(self, client, auth_headers_user):
        """Test that the /user/me PATCH endpoint can update email notification preferences"""
        # Update profile with email notification preference
        update_data = {"name": "Updated Name", "email_notifications_enabled": False}

        response = client.patch(
            "/api/v1/user/me", json=update_data, headers=auth_headers_user
        )

        assert response.status_code == 200
        data = response.get_json()

        # Verify the response includes updated preference
        assert data["data"]["email_notifications_enabled"] is False
        assert data["data"]["name"] == "Updated Name"

        # Test updating only email notifications preference
        update_data = {"email_notifications_enabled": True}

        response = client.patch(
            "/api/v1/user/me", json=update_data, headers=auth_headers
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["data"]["email_notifications_enabled"] is True

    def test_profile_get_api_includes_email_notifications(self, client, auth_headers_user):
        """Test that the /user/me GET endpoint includes email notification preferences"""
        response = client.get("/api/v1/user/me", headers=auth_headers_user)

        assert response.status_code == 200
        data = response.get_json()

        # Verify email_notifications_enabled is included
        assert "email_notifications_enabled" in data["data"]
        # Default should be True
        assert data["data"]["email_notifications_enabled"] is True
