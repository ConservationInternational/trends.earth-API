"""
Unit tests for permission utilities and SUPERADMIN role functionality
"""

from unittest.mock import Mock

from gefapi.config import SETTINGS
from gefapi.utils.permissions import (
    can_access_admin_features,
    can_change_user_password,
    can_change_user_role,
    can_delete_user,
    can_manage_users,
    can_update_user_profile,
    is_admin_or_higher,
    is_superadmin,
)

ENVIRONMENT_USER_EMAIL = SETTINGS["API_ENVIRONMENT_USER"]


class TestPermissionUtilities:
    """Test permission utility functions"""

    def create_mock_user(self, role, email="test@example.com"):
        """Create a mock user object"""
        user = Mock()
        user.role = role
        user.email = email
        return user

    def test_is_superadmin_with_superadmin_role(self):
        """Test is_superadmin returns True for SUPERADMIN role"""
        user = self.create_mock_user("SUPERADMIN")
        assert is_superadmin(user) is True

    def test_is_superadmin_with_environment_user_email(self):
        """Test is_superadmin returns True for the automation account email"""
        user = self.create_mock_user("USER", ENVIRONMENT_USER_EMAIL)
        assert is_superadmin(user) is True

    def test_is_superadmin_with_admin_role(self):
        """Test is_superadmin returns False for ADMIN role"""
        user = self.create_mock_user("ADMIN")
        assert is_superadmin(user) is False

    def test_is_superadmin_with_user_role(self):
        """Test is_superadmin returns False for USER role"""
        user = self.create_mock_user("USER")
        assert is_superadmin(user) is False

    def test_is_admin_or_higher_with_superadmin(self):
        """Test is_admin_or_higher returns True for SUPERADMIN"""
        user = self.create_mock_user("SUPERADMIN")
        assert is_admin_or_higher(user) is True

    def test_is_admin_or_higher_with_admin(self):
        """Test is_admin_or_higher returns True for ADMIN"""
        user = self.create_mock_user("ADMIN")
        assert is_admin_or_higher(user) is True

    def test_is_admin_or_higher_with_user(self):
        """Test is_admin_or_higher returns False for USER"""
        user = self.create_mock_user("USER")
        assert is_admin_or_higher(user) is False

    def test_is_admin_or_higher_with_environment_user_email(self):
        """Test is_admin_or_higher returns True for the automation account email"""
        user = self.create_mock_user("USER", ENVIRONMENT_USER_EMAIL)
        assert is_admin_or_higher(user) is True

    def test_can_manage_users_superadmin_only(self):
        """Test can_manage_users returns True only for SUPERADMIN"""
        superadmin = self.create_mock_user("SUPERADMIN")
        admin = self.create_mock_user("ADMIN")
        user = self.create_mock_user("USER")
        env_user = self.create_mock_user("USER", ENVIRONMENT_USER_EMAIL)

        assert can_manage_users(superadmin) is True
        assert can_manage_users(admin) is False
        assert can_manage_users(user) is False
        assert can_manage_users(env_user) is True

    def test_can_change_user_role_superadmin_only(self):
        """Test can_change_user_role returns True only for SUPERADMIN"""
        superadmin = self.create_mock_user("SUPERADMIN")
        admin = self.create_mock_user("ADMIN")
        user = self.create_mock_user("USER")

        assert can_change_user_role(superadmin) is True
        assert can_change_user_role(admin) is False
        assert can_change_user_role(user) is False

    def test_can_delete_user_superadmin_only(self):
        """Test can_delete_user returns True only for SUPERADMIN"""
        superadmin = self.create_mock_user("SUPERADMIN")
        admin = self.create_mock_user("ADMIN")
        user = self.create_mock_user("USER")

        assert can_delete_user(superadmin) is True
        assert can_delete_user(admin) is False
        assert can_delete_user(user) is False

    def test_can_change_user_password_admin_or_higher(self):
        """Test can_change_user_password returns True for ADMIN and SUPERADMIN

        Note: ADMIN can change passwords, but the route-level check
        (can_admin_change_user_password) prevents them from changing SUPERADMIN's.
        """
        superadmin = self.create_mock_user("SUPERADMIN")
        admin = self.create_mock_user("ADMIN")
        user = self.create_mock_user("USER")

        assert can_change_user_password(superadmin) is True
        assert can_change_user_password(admin) is True
        assert can_change_user_password(user) is False

    def test_can_update_user_profile_superadmin_only(self):
        """Test can_update_user_profile returns True only for SUPERADMIN"""
        superadmin = self.create_mock_user("SUPERADMIN")
        admin = self.create_mock_user("ADMIN")
        user = self.create_mock_user("USER")

        assert can_update_user_profile(superadmin) is True
        assert can_update_user_profile(admin) is False
        assert can_update_user_profile(user) is False

    def test_can_access_admin_features_admin_or_higher(self):
        """Test can_access_admin_features returns True for ADMIN and SUPERADMIN"""
        superadmin = self.create_mock_user("SUPERADMIN")
        admin = self.create_mock_user("ADMIN")
        user = self.create_mock_user("USER")

        assert can_access_admin_features(superadmin) is True
        assert can_access_admin_features(admin) is True
        assert can_access_admin_features(user) is False

    def test_all_superadmin_permissions_for_environment_user_email(self):
        """Test that the automation user has all superadmin permissions regardless of role"""
        env_user = self.create_mock_user("USER", ENVIRONMENT_USER_EMAIL)

        assert is_superadmin(env_user) is True
        assert is_admin_or_higher(env_user) is True
        assert can_manage_users(env_user) is True
        assert can_change_user_role(env_user) is True
        assert can_delete_user(env_user) is True
        assert can_change_user_password(env_user) is True
        assert can_update_user_profile(env_user) is True
        assert can_access_admin_features(env_user) is True

    def test_permission_hierarchy(self):
        """Test that permission hierarchy is correctly implemented"""
        superadmin = self.create_mock_user("SUPERADMIN")
        admin = self.create_mock_user("ADMIN")
        user = self.create_mock_user("USER")

        # SUPERADMIN should have all permissions
        superadmin_permissions = [
            is_superadmin(superadmin),
            is_admin_or_higher(superadmin),
            can_manage_users(superadmin),
            can_change_user_role(superadmin),
            can_delete_user(superadmin),
            can_change_user_password(superadmin),
            can_update_user_profile(superadmin),
            can_access_admin_features(superadmin),
        ]
        assert all(superadmin_permissions)

        # ADMIN should have admin features but not user management
        # Note: ADMIN can change passwords (but not SUPERADMIN's - checked at route level)
        admin_permissions = [
            not is_superadmin(admin),
            is_admin_or_higher(admin),
            not can_manage_users(admin),
            not can_change_user_role(admin),
            not can_delete_user(admin),
            can_change_user_password(admin),  # ADMIN can change passwords
            not can_update_user_profile(admin),
            can_access_admin_features(admin),
        ]
        assert all(admin_permissions)

        # USER should have no admin permissions
        user_permissions = [
            not is_superadmin(user),
            not is_admin_or_higher(user),
            not can_manage_users(user),
            not can_change_user_role(user),
            not can_delete_user(user),
            not can_change_user_password(user),
            not can_update_user_profile(user),
            not can_access_admin_features(user),
        ]
        assert all(user_permissions)
