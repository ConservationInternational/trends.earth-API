"""
Tests for script access control functionality
"""

import pytest

from gefapi import db
from gefapi.models import Script, User
from gefapi.utils.script_access import (
    add_role_to_script,
    add_user_to_script,
    can_manage_script_access,
    clear_script_restrictions,
    get_access_summary,
    remove_role_from_script,
    remove_user_from_script,
    set_script_roles,
    set_script_users,
)


@pytest.mark.usefixtures("app")
class TestScriptAccessControl:
    """Test script access control functionality"""

    def setup_method(self, method):
        """Clean up test data before each test"""
        # This will be called before each test method
        pass

    def teardown_method(self, method):
        """Clean up test data after each test"""
        # Clean up any test users we may have created to avoid conflicts
        test_emails = [
            "owner@test.com",
            "admin@test.com",
            "user@test.com",
            "allowed@test.com",
            "denied@test.com",
            "user1@test.com",
            "user2@test.com",
        ]
        for email in test_emails:
            try:
                user = User.query.filter_by(email=email).first()
                if user:
                    db.session.delete(user)
                db.session.commit()
            except Exception:
                db.session.rollback()

    def create_test_user(self, app, email, role="USER"):
        """Create a test user"""
        # Check if user already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            # Update existing user's role and return it
            existing_user.role = role
            existing_user.password = existing_user.set_password("test123")
            db.session.add(existing_user)
            db.session.commit()
            return existing_user

        user = User(
            email=email,
            password="placeholder",  # Will be overwritten with hashed password
            name="Test User",
            country="Test Country",
            institution="Test Institution",
            role=role,
        )
        user.password = user.set_password("test123")
        db.session.add(user)
        db.session.commit()
        return user

    def create_test_script(self, app, user, name="Test Script"):
        """Create a test script"""
        # Ensure user is in current session
        if user not in db.session:
            user = db.session.merge(user)

        script = Script(
            name=name,
            slug=f"test-script-{name.lower().replace(' ', '-')}",
            user_id=user.id,
        )
        db.session.add(script)
        db.session.commit()
        return script

    def test_script_can_access_owner(self, app):
        """Test that script owner can always access their script"""
        with app.app_context():
            user = self.create_test_user(app, "owner@test.com")
            script = self.create_test_script(app, user)

            assert script.can_access(user) is True

    def test_script_can_access_admin(self, app):
        """Test that admin can always access any script"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            admin = self.create_test_user(app, "admin@test.com", "ADMIN")
            script = self.create_test_script(app, owner)

            # Admin should access even restricted script
            set_script_roles(script, ["SUPERADMIN"])
            db.session.commit()

            assert script.can_access(admin) is True

    def test_script_can_access_public(self, app):
        """Test that public scripts are accessible to anyone"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            user = self.create_test_user(app, "user@test.com")
            script = self.create_test_script(app, owner)

            script.public = True
            db.session.commit()

            assert script.can_access(user) is True

    def test_script_can_access_unrestricted(self, app):
        """Test that unrestricted private scripts are accessible to any authenticated user"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            user = self.create_test_user(app, "user@test.com")
            script = self.create_test_script(app, owner)

            # Default: not public, not restricted
            assert script.public is False
            assert script.restricted is False

            assert script.can_access(user) is True

    def test_script_access_role_restriction(self, app):
        """Test role-based access restrictions"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            admin = self.create_test_user(app, "admin@test.com", "ADMIN")
            user = self.create_test_user(app, "user@test.com", "USER")
            script = self.create_test_script(app, owner)

            # Restrict to ADMIN role only
            set_script_roles(script, ["ADMIN"])
            db.session.commit()

            assert script.can_access(admin) is True
            assert script.can_access(user) is False

    def test_script_access_user_restriction(self, app):
        """Test user-based access restrictions"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            allowed_user = self.create_test_user(app, "allowed@test.com")
            denied_user = self.create_test_user(app, "denied@test.com")
            script = self.create_test_script(app, owner)

            # Restrict to specific user only
            set_script_users(script, [str(allowed_user.id)])
            db.session.commit()

            assert script.can_access(allowed_user) is True
            assert script.can_access(denied_user) is False

    def test_script_access_hybrid_restriction(self, app):
        """Test combined role and user restrictions"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            admin = self.create_test_user(app, "admin@test.com", "ADMIN")
            allowed_user = self.create_test_user(app, "allowed@test.com", "USER")
            denied_user = self.create_test_user(app, "denied@test.com", "USER")
            script = self.create_test_script(app, owner)

            # Allow ADMIN role OR specific user
            set_script_roles(script, ["ADMIN"])
            add_user_to_script(script, str(allowed_user.id))
            db.session.commit()

            assert script.can_access(admin) is True  # Has ADMIN role
            assert script.can_access(allowed_user) is True  # In allowed users
            assert script.can_access(denied_user) is False  # Neither role nor user

    def test_set_script_roles(self, app):
        """Test setting allowed roles"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            script = self.create_test_script(app, owner)

            set_script_roles(script, ["ADMIN", "SUPERADMIN"])
            db.session.commit()

            assert script.restricted is True
            assert script.get_allowed_roles() == ["ADMIN", "SUPERADMIN"]

    def test_set_script_users(self, app):
        """Test setting allowed users"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            user1 = self.create_test_user(app, "user1@test.com")
            user2 = self.create_test_user(app, "user2@test.com")
            script = self.create_test_script(app, owner)

            set_script_users(script, [str(user1.id), str(user2.id)])
            db.session.commit()

            assert script.restricted is True
            assert script.get_allowed_users() == [str(user1.id), str(user2.id)]

    def test_add_remove_user_access(self, app):
        """Test adding and removing user access"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            user = self.create_test_user(app, "user@test.com")
            script = self.create_test_script(app, owner)

            # Add user
            add_user_to_script(script, str(user.id))
            db.session.commit()

            assert str(user.id) in script.get_allowed_users()
            assert script.restricted is True

            # Remove user
            remove_user_from_script(script, str(user.id))
            db.session.commit()

            assert str(user.id) not in script.get_allowed_users()

    def test_add_remove_role_access(self, app):
        """Test adding and removing role access"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            script = self.create_test_script(app, owner)

            # Add role
            add_role_to_script(script, "ADMIN")
            db.session.commit()

            assert "ADMIN" in script.get_allowed_roles()
            assert script.restricted is True

            # Remove role
            remove_role_from_script(script, "ADMIN")
            db.session.commit()

            assert "ADMIN" not in script.get_allowed_roles()

    def test_clear_restrictions(self, app):
        """Test clearing all access restrictions"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            user = self.create_test_user(app, "user@test.com")
            script = self.create_test_script(app, owner)

            # Add restrictions
            set_script_roles(script, ["ADMIN"])
            set_script_users(script, [str(user.id)])
            db.session.commit()

            assert script.restricted is True

            # Clear restrictions
            clear_script_restrictions(script)
            db.session.commit()

            assert script.restricted is False
            assert script.allowed_roles is None
            assert script.allowed_users is None

    def test_get_access_summary(self, app):
        """Test getting access summary"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            script = self.create_test_script(app, owner)

            # Test owner_only
            summary = get_access_summary(script)
            assert summary["access_type"] == "owner_only"
            assert summary["restricted"] is False
            assert summary["public"] is False

            # Test public
            script.public = True
            db.session.commit()
            summary = get_access_summary(script)
            assert summary["access_type"] == "public"

            # Test restricted
            script.public = False
            set_script_roles(script, ["ADMIN"])
            db.session.commit()
            summary = get_access_summary(script)
            assert summary["access_type"] == "restricted"
            assert summary["allowed_roles"] == ["ADMIN"]

    def test_can_manage_script_access(self, app):
        """Test permissions for managing script access"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            admin = self.create_test_user(app, "admin@test.com", "ADMIN")
            user = self.create_test_user(app, "user@test.com")
            script = self.create_test_script(app, owner)

            # Owner can manage
            assert can_manage_script_access(owner, script) is True

            # Admin can manage
            assert can_manage_script_access(admin, script) is True

            # Regular user cannot manage
            assert can_manage_script_access(user, script) is False

    def test_script_serialization_includes_access_control(self, app):
        """Test that script serialization includes access control info when requested"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            script = self.create_test_script(app, owner)

            set_script_roles(script, ["ADMIN"])
            db.session.commit()

            # Test with access_control included
            serialized = script.serialize(include=["access_control"], user=owner)
            assert "allowed_roles" in serialized
            assert serialized["allowed_roles"] == ["ADMIN"]
            assert serialized["restricted"] is True

    def test_invalid_json_handling(self, app):
        """Test handling of invalid JSON in access control fields"""
        with app.app_context():
            owner = self.create_test_user(app, "owner@test.com")
            script = self.create_test_script(app, owner)

            # Set invalid JSON
            script.allowed_roles = "invalid json"
            script.allowed_users = "invalid json"
            db.session.commit()

            # Should return empty lists for invalid JSON
            assert script.get_allowed_roles() == []
            assert script.get_allowed_users() == []
