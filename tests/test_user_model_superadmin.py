"""
Integration tests for User model with SUPERADMIN role functionality
"""

import uuid

import pytest

from gefapi import db
from gefapi.models import User


def generate_unique_email(prefix="test"):
    """Generate a unique email for testing"""
    unique_id = uuid.uuid4().hex[:8]
    return f"{prefix}-{unique_id}@example.com"


@pytest.mark.usefixtures("app")
class TestUserModelSuperAdmin:
    """Test User model with SUPERADMIN role"""

    def test_create_superadmin_user(self, app):
        """Test creating a user with SUPERADMIN role"""
        with app.app_context():
            email = generate_unique_email("test-superadmin")

            user = User(
                email=email,
                password="password123",
                name="Test SuperAdmin",
                country="Test Country",
                institution="Test Institution",
                role="SUPERADMIN",
            )
            db.session.add(user)
            db.session.commit()

            # Verify user was created with correct role
            created_user = User.query.filter_by(email=email).first()
            assert created_user is not None
            assert created_user.role == "SUPERADMIN"
            assert created_user.name == "Test SuperAdmin"

            # Clean up
            db.session.delete(created_user)
            db.session.commit()

    def test_user_role_validation_in_constructor(self, app):
        """Test that User constructor validates roles"""
        with app.app_context():
            # Valid role should be accepted
            user = User(
                email=generate_unique_email("test-valid-role"),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="SUPERADMIN",
            )
            assert user.role == "SUPERADMIN"

            # Invalid role should default to USER
            user_invalid = User(
                email=generate_unique_email("test-invalid-role"),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="INVALID_ROLE",
            )
            assert user_invalid.role == "USER"

    def test_default_role_is_user(self, app):
        """Test that default role is USER when not specified"""
        with app.app_context():
            user = User(
                email=generate_unique_email("test-default-role"),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
            )
            assert user.role == "USER"

    def test_user_serialization_includes_role(self, app):
        """Test that user serialization includes the role field"""
        with app.app_context():
            user = User(
                email=generate_unique_email("test-serialization"),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="SUPERADMIN",
            )
            db.session.add(user)
            db.session.commit()

            serialized = user.serialize()
            assert "role" in serialized
            assert serialized["role"] == "SUPERADMIN"

            # Clean up
            db.session.delete(user)
            db.session.commit()

    def test_user_serialization_with_exclude(self, app):
        """Test that user serialization respects exclude parameter"""
        with app.app_context():
            user = User(
                email=generate_unique_email("test-exclude"),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="SUPERADMIN",
            )
            db.session.add(user)
            db.session.commit()

            # Test excluding role
            serialized = user.serialize(exclude=["role"])
            assert "role" not in serialized
            assert "email" in serialized

            # Test excluding multiple fields
            serialized = user.serialize(exclude=["role", "country", "institution"])
            assert "role" not in serialized
            assert "country" not in serialized
            assert "institution" not in serialized
            assert "email" in serialized
            assert "name" in serialized

            # Clean up
            db.session.delete(user)
            db.session.commit()

    def test_password_hashing_works_for_superadmin(self, app):
        """Test that password hashing works correctly for SUPERADMIN users"""
        with app.app_context():
            user = User(
                email=generate_unique_email("test-password"),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="SUPERADMIN",
            )
            db.session.add(user)
            db.session.commit()

            # Verify password is hashed
            assert user.password != "password123"

            # Verify password checking works
            assert user.check_password("password123") is True
            assert user.check_password("wrongpassword") is False

            # Clean up
            db.session.delete(user)
            db.session.commit()

    def test_jwt_token_generation_for_superadmin(self, app):
        """Test that JWT token generation works for SUPERADMIN users"""
        with app.app_context():
            user = User(
                email=generate_unique_email("test-token"),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
                role="SUPERADMIN",
            )
            db.session.add(user)
            db.session.commit()

            # Generate token
            token = user.get_token()
            assert token is not None
            assert isinstance(token, str)
            assert len(token) > 0

            # Clean up
            db.session.delete(user)
            db.session.commit()

    def test_unique_email_constraint(self, app):
        """Test that email uniqueness is enforced for all roles"""
        with app.app_context():
            email = generate_unique_email("unique-test")

            # Create first user
            user1 = User(
                email=email,
                password="password123",
                name="Test User 1",
                country="Test Country",
                institution="Test Institution",
                role="SUPERADMIN",
            )
            db.session.add(user1)
            db.session.commit()

            # Try to create second user with same email
            user2 = User(
                email=email,
                password="password456",
                name="Test User 2",
                country="Test Country",
                institution="Test Institution",
                role="ADMIN",
            )
            db.session.add(user2)

            # This should raise an integrity error
            with pytest.raises(Exception):  # Could be IntegrityError or similar
                db.session.commit()

            # Clean up
            db.session.rollback()
            db.session.delete(user1)
            db.session.commit()

    def test_role_query_filtering(self, app):
        """Test querying users by role"""
        with app.app_context():
            # Generate unique emails for test isolation
            superadmin_email = generate_unique_email("query-superadmin")
            admin_email = generate_unique_email("query-admin")
            user_email = generate_unique_email("query-user")

            # Create test users with different roles
            superadmin = User(
                email=superadmin_email,
                password="password123",
                name="Query SuperAdmin",
                country="Test Country",
                institution="Test Institution",
                role="SUPERADMIN",
            )
            admin = User(
                email=admin_email,
                password="password123",
                name="Query Admin",
                country="Test Country",
                institution="Test Institution",
                role="ADMIN",
            )
            user = User(
                email=user_email,
                password="password123",
                name="Query User",
                country="Test Country",
                institution="Test Institution",
                role="USER",
            )

            db.session.add_all([superadmin, admin, user])
            db.session.commit()

            # Query for each role
            superadmin_users = User.query.filter_by(role="SUPERADMIN").all()
            admin_users = User.query.filter_by(role="ADMIN").all()
            regular_users = User.query.filter_by(role="USER").all()

            # Verify results
            superadmin_emails = [u.email for u in superadmin_users]
            admin_emails = [u.email for u in admin_users]
            user_emails = [u.email for u in regular_users]

            assert superadmin_email in superadmin_emails
            assert admin_email in admin_emails
            assert user_email in user_emails

            # Clean up
            db.session.delete(superadmin)
            db.session.delete(admin)
            db.session.delete(user)
            db.session.commit()

    def test_user_repr_includes_email(self, app):
        """Test that User __repr__ method works correctly"""
        with app.app_context():
            email = generate_unique_email("repr-test")
            user = User(
                email=email,
                password="password123",
                name="Repr Test User",
                country="Test Country",
                institution="Test Institution",
                role="SUPERADMIN",
            )

            repr_str = repr(user)
            assert email in repr_str
            assert "User" in repr_str
