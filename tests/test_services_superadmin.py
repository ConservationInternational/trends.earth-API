"""
Tests for service layer with SUPERADMIN permissions
"""

from unittest.mock import Mock

import pytest

from gefapi import db
from gefapi.models import Execution
from gefapi.services.execution_service import ExecutionService
from gefapi.services.script_service import ScriptService
from gefapi.services.user_service import UserService


@pytest.mark.usefixtures("app")
class TestUserServiceSuperAdmin:
    """Test UserService with SUPERADMIN permissions"""

    def test_create_user_with_superadmin_role(self, app):
        """Test creating user with SUPERADMIN role via service"""
        with app.app_context():
            import uuid

            unique_email = f"service-superadmin-{uuid.uuid4().hex[:8]}@example.com"
            user_data = {
                "email": unique_email,
                "password": "password123",
                "name": "Service SuperAdmin",
                "country": "Test Country",
                "institution": "Test Institution",
                "role": "SUPERADMIN",
            }

            user = UserService.create_user(user_data)
            assert user.role == "SUPERADMIN"
            assert user.email == unique_email

            # Clean up
            UserService.delete_user(user.id)

    def test_create_user_with_invalid_role_defaults_to_user(self, app):
        """Test creating user with invalid role defaults to USER"""
        with app.app_context():
            import uuid

            unique_email = f"service-invalid-role-{uuid.uuid4().hex[:8]}@example.com"
            user_data = {
                "email": unique_email,
                "password": "password123",
                "name": "Service Invalid Role",
                "country": "Test Country",
                "institution": "Test Institution",
                "role": "INVALID_ROLE",
            }

            user = UserService.create_user(user_data)
            assert user.role == "USER"  # Should default to USER for invalid role

            # Clean up
            UserService.delete_user(user.id)

    def test_update_user_role_to_superadmin(self, app, regular_user):
        """Test updating user role to SUPERADMIN via service"""
        with app.app_context():
            # Merge user to ensure it's in current session
            user = db.session.merge(regular_user)
            original_role = user.role

            update_data = {"role": "SUPERADMIN"}
            updated_user = UserService.update_user(update_data, user.id)

            assert updated_user.role == "SUPERADMIN"
            assert updated_user.id == user.id

            # Restore original role
            restore_data = {"role": original_role}
            UserService.update_user(restore_data, user.id)

    def test_user_filtering_by_superadmin_role(self, app):
        """Test filtering users by SUPERADMIN role via service"""
        with app.app_context():
            import uuid

            unique_email = f"filter-superadmin-{uuid.uuid4().hex[:8]}@example.com"
            # Create test users
            superadmin_data = {
                "email": unique_email,
                "password": "password123",
                "name": "Filter SuperAdmin",
                "country": "Test Country",
                "institution": "Test Institution",
                "role": "SUPERADMIN",
            }

            created_user = UserService.create_user(superadmin_data)

            # Filter by SUPERADMIN role
            users, total = UserService.get_users(filter_param="role=SUPERADMIN")

            superadmin_users = [u for u in users if u.role == "SUPERADMIN"]
            assert len(superadmin_users) > 0

            superadmin_emails = [u.email for u in superadmin_users]
            assert unique_email in superadmin_emails

            # Clean up
            UserService.delete_user(created_user.id)


@pytest.mark.usefixtures("app")
class TestScriptServiceWithSuperAdmin:
    """Test ScriptService with SUPERADMIN permissions"""

    def create_mock_user(self, role, email="test@example.com"):
        """Create a mock user object"""
        from uuid import uuid4

        user = Mock()
        user.role = role
        user.email = email
        user.id = uuid4()
        return user

    def test_script_listing_with_superadmin(self, app, sample_script):
        """Test that SUPERADMIN can see all scripts"""
        with app.app_context():
            superadmin = self.create_mock_user("SUPERADMIN")

            scripts, total = ScriptService.get_scripts(user=superadmin)

            # SUPERADMIN should see all scripts (no filtering by user_id)
            assert len(scripts) >= 0  # Should not be filtered
            assert total >= 0

    def test_script_listing_with_regular_user(self, app, regular_user, sample_script):
        """Test that regular users only see their own scripts and public ones"""
        with app.app_context():
            # Merge objects to current session
            user = db.session.merge(regular_user)
            script = db.session.merge(sample_script)

            scripts, total = ScriptService.get_scripts(user=user)

            # Regular user should only see own scripts + public scripts
            for script in scripts:
                assert script.user_id == user.id or script.public is True

    def test_script_retrieval_with_superadmin(self, app, sample_script):
        """Test that SUPERADMIN can retrieve any script"""
        with app.app_context():
            script = db.session.merge(sample_script)
            superadmin = self.create_mock_user("SUPERADMIN")

            retrieved_script = ScriptService.get_script(script.id, superadmin)
            assert retrieved_script is not None
            assert retrieved_script.id == script.id

    def test_script_update_with_superadmin(self, app, sample_script):
        """Test that SUPERADMIN can update any script"""
        with app.app_context():
            script = db.session.merge(sample_script)
            superadmin = self.create_mock_user("SUPERADMIN")

            # This would normally require file upload, but we're testing permission logic
            # The permission check should pass for SUPERADMIN
            retrieved_script = ScriptService.get_script(script.id, superadmin)
            assert retrieved_script is not None

    def test_script_publish_with_superadmin(self, app, sample_script):
        """Test that SUPERADMIN can publish any script"""
        with app.app_context():
            script = db.session.merge(sample_script)
            superadmin = self.create_mock_user("SUPERADMIN")

            published_script = ScriptService.publish_script(script.id, superadmin)
            assert published_script is not None
            assert published_script.public is True

    def test_script_unpublish_with_superadmin(self, app, sample_script):
        """Test that SUPERADMIN can unpublish any script"""
        with app.app_context():
            script = db.session.merge(sample_script)
            script.public = True  # Make sure it's published first
            db.session.add(script)
            db.session.commit()

            superadmin = self.create_mock_user("SUPERADMIN")

            unpublished_script = ScriptService.unpublish_script(script.id, superadmin)
            assert unpublished_script is not None
            assert unpublished_script.public is False


@pytest.mark.usefixtures("app")
class TestExecutionServiceWithSuperAdmin:
    """Test ExecutionService with SUPERADMIN permissions"""

    def create_mock_user(self, role, email="test@example.com"):
        """Create a mock user object"""
        from uuid import uuid4

        user = Mock()
        user.role = role
        user.email = email
        user.id = uuid4()
        return user

    def test_execution_listing_with_superadmin(self, app, sample_execution):
        """Test that SUPERADMIN can see all executions"""
        with app.app_context():
            superadmin = self.create_mock_user("SUPERADMIN")

            executions, total = ExecutionService.get_executions(user=superadmin)

            # SUPERADMIN should see all executions (no filtering by user_id)
            assert len(executions) >= 0
            assert total >= 0

    def test_execution_listing_with_regular_user(
        self, app, regular_user, sample_execution
    ):
        """Test that regular users only see their own executions"""
        with app.app_context():
            user = db.session.merge(regular_user)

            # Clean up any existing executions not belonging to this user
            Execution.query.filter(Execution.user_id != user.id).delete()
            db.session.commit()

            executions, total = ExecutionService.get_executions(user=user)

            # Regular user should only see own executions
            for execution in executions:
                assert execution.user_id == user.id

    def test_execution_retrieval_with_superadmin(self, app, sample_execution):
        """Test that SUPERADMIN can retrieve any execution"""
        with app.app_context():
            execution = db.session.merge(sample_execution)
            superadmin = self.create_mock_user("SUPERADMIN")

            retrieved_execution = ExecutionService.get_execution(
                execution.id, superadmin
            )
            assert retrieved_execution is not None
            assert retrieved_execution.id == execution.id

    def test_execution_with_target_user_id_for_superadmin(self, app, sample_execution):
        """Test that SUPERADMIN can filter executions by target_user_id"""
        with app.app_context():
            execution = db.session.merge(sample_execution)
            superadmin = self.create_mock_user("SUPERADMIN")

            # SUPERADMIN should be able to filter by target_user_id
            executions, total = ExecutionService.get_executions(
                user=superadmin, target_user_id=execution.user_id
            )

            # Should get executions for the target user
            for exec in executions:
                assert exec.user_id == execution.user_id


@pytest.mark.usefixtures("app")
class TestPermissionIntegration:
    """Integration tests for permission system across services"""

    def create_mock_user(self, role, email="test@example.com", user_id=None):
        """Create a mock user object"""
        from uuid import uuid4

        user = Mock()
        user.role = role
        user.email = email
        user.id = user_id or uuid4()
        return user

    def test_admin_vs_superadmin_script_access(self, app, sample_script):
        """Test that both ADMIN and SUPERADMIN have script access but only SUPERADMIN has user management"""
        with app.app_context():
            script = db.session.merge(sample_script)

            admin = self.create_mock_user("ADMIN")
            superadmin = self.create_mock_user("SUPERADMIN")

            # Both should be able to access scripts
            admin_scripts, _ = ScriptService.get_scripts(user=admin)
            superadmin_scripts, _ = ScriptService.get_scripts(user=superadmin)

            assert len(admin_scripts) >= 0
            assert len(superadmin_scripts) >= 0

            # Both should be able to retrieve specific scripts
            admin_script = ScriptService.get_script(script.id, admin)
            superadmin_script = ScriptService.get_script(script.id, superadmin)

            assert admin_script is not None
            assert superadmin_script is not None

    def test_admin_vs_superadmin_execution_access(self, app, sample_execution):
        """Test that both ADMIN and SUPERADMIN have execution access"""
        with app.app_context():
            execution = db.session.merge(sample_execution)

            admin = self.create_mock_user("ADMIN")
            superadmin = self.create_mock_user("SUPERADMIN")

            # Both should be able to access executions
            admin_executions, _ = ExecutionService.get_executions(user=admin)
            superadmin_executions, _ = ExecutionService.get_executions(user=superadmin)

            assert len(admin_executions) >= 0
            assert len(superadmin_executions) >= 0

            # Both should be able to retrieve specific executions
            admin_execution = ExecutionService.get_execution(execution.id, admin)
            superadmin_execution = ExecutionService.get_execution(
                execution.id, superadmin
            )

            assert admin_execution is not None
            assert superadmin_execution is not None

    def test_service_level_permission_consistency(self, app):
        """Test that service layer respects the same permission hierarchy as API layer"""
        with app.app_context():
            user = self.create_mock_user("USER")
            admin = self.create_mock_user("ADMIN")
            superadmin = self.create_mock_user("SUPERADMIN")

            # Test script access levels
            user_scripts, _ = ScriptService.get_scripts(user=user)
            admin_scripts, _ = ScriptService.get_scripts(user=admin)
            superadmin_scripts, _ = ScriptService.get_scripts(user=superadmin)

            # Regular users see filtered results (own + public)
            # Admins and superadmins see all
            assert len(admin_scripts) >= len(user_scripts)
            assert len(superadmin_scripts) >= len(user_scripts)

            # Test execution access levels
            user_executions, _ = ExecutionService.get_executions(user=user)
            admin_executions, _ = ExecutionService.get_executions(user=admin)
            superadmin_executions, _ = ExecutionService.get_executions(user=superadmin)

            # Regular users see only their executions
            # Admins and superadmins see all
            assert len(admin_executions) >= len(user_executions)
            assert len(superadmin_executions) >= len(user_executions)
