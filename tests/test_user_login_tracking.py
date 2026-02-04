"""
Test cases for user login tracking, email verification, and user cleanup tasks.

These tests verify:
1. last_login_at is updated on successful authentication
2. email_verified and email_verified_at fields work correctly
3. User cleanup tasks properly remove unverified/inactive users
4. Legacy users (created before feature launch) are NOT deleted

These are integration tests that require a database connection.
Run with: ./run_tests.sh tests/test_user_login_tracking.py
"""

import datetime
import os
from unittest.mock import patch

from conftest import STRONG_GENERIC_PASSWORD
import pytest

from gefapi import db
from gefapi.models import User
from gefapi.services.user_service import UserService


@pytest.mark.integration
class TestLastLoginTracking:
    """Test that last_login_at is properly tracked on authentication"""

    def test_last_login_at_updated_on_successful_auth(self, app):
        """Test that last_login_at is set when user authenticates successfully"""
        with app.app_context():
            # Create a fresh user
            user = User(
                email="login_test@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Login Test User",
                role="USER",
                country="Test",
                institution="Test",
            )
            db.session.add(user)
            db.session.commit()
            user_id = user.id

            # Verify last_login_at is initially None
            user = User.query.get(user_id)
            assert user.last_login_at is None

            # Authenticate
            before_login = datetime.datetime.utcnow()
            authenticated_user = UserService.authenticate_user(
                "login_test@example.com", STRONG_GENERIC_PASSWORD
            )
            after_login = datetime.datetime.utcnow()

            assert authenticated_user is not None

            # Reload user and check last_login_at
            user = User.query.filter_by(email="login_test@example.com").first()
            assert user.last_login_at is not None
            assert before_login <= user.last_login_at <= after_login

            # Clean up
            db.session.delete(user)
            db.session.commit()

    def test_last_login_at_updated_on_subsequent_logins(self, app):
        """Test that last_login_at is updated on each subsequent login"""
        with app.app_context():
            # Create user
            user = User(
                email="multi_login@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Multi Login User",
                role="USER",
                country="Test",
                institution="Test",
            )
            db.session.add(user)
            db.session.commit()

            # First login
            UserService.authenticate_user(
                "multi_login@example.com", STRONG_GENERIC_PASSWORD
            )
            user = User.query.filter_by(email="multi_login@example.com").first()
            first_login_time = user.last_login_at

            # Wait a tiny bit and login again
            import time

            time.sleep(0.1)

            UserService.authenticate_user(
                "multi_login@example.com", STRONG_GENERIC_PASSWORD
            )
            user = User.query.filter_by(email="multi_login@example.com").first()
            second_login_time = user.last_login_at

            # Second login should be later
            assert second_login_time > first_login_time

            # Clean up
            db.session.delete(user)
            db.session.commit()

    def test_last_login_at_not_updated_on_failed_auth(self, app):
        """Test that last_login_at is NOT updated on failed authentication"""
        with app.app_context():
            # Create user
            user = User(
                email="failed_auth@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Failed Auth User",
                role="USER",
                country="Test",
                institution="Test",
            )
            db.session.add(user)
            db.session.commit()

            # Attempt authentication with wrong password
            result = UserService.authenticate_user(
                "failed_auth@example.com", "WrongPassword123!"
            )
            assert result is None

            # last_login_at should still be None
            user = User.query.filter_by(email="failed_auth@example.com").first()
            assert user.last_login_at is None

            # Clean up
            db.session.delete(user)
            db.session.commit()

    def test_last_login_at_serialized_in_user_response(self, app):
        """Test that last_login_at is included in serialized user data"""
        with app.app_context():
            user = User(
                email="serialize_test@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Serialize Test",
                role="USER",
                country="Test",
                institution="Test",
            )
            db.session.add(user)
            db.session.commit()

            # Before login - should be None
            serialized = user.serialize()
            assert "last_login_at" in serialized
            assert serialized["last_login_at"] is None

            # After login - should have timestamp
            UserService.authenticate_user(
                "serialize_test@example.com", STRONG_GENERIC_PASSWORD
            )
            user = User.query.filter_by(email="serialize_test@example.com").first()
            serialized = user.serialize()
            assert serialized["last_login_at"] is not None

            # Clean up
            db.session.delete(user)
            db.session.commit()


@pytest.mark.integration
class TestEmailVerificationTracking:
    """Test email verification field tracking"""

    def test_new_user_has_email_verified_false(self, app):
        """Test that new users have email_verified=False by default"""
        with app.app_context():
            user = User(
                email="new_user_verify@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="New User",
                role="USER",
                country="Test",
                institution="Test",
            )
            db.session.add(user)
            db.session.commit()

            assert user.email_verified is False
            assert user.email_verified_at is None

            # Clean up
            db.session.delete(user)
            db.session.commit()

    def test_email_verification_can_be_set(self, app):
        """Test that email verification fields can be updated"""
        with app.app_context():
            user = User(
                email="verify_test@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Verify Test",
                role="USER",
                country="Test",
                institution="Test",
            )
            db.session.add(user)
            db.session.commit()

            # Simulate email verification
            now = datetime.datetime.utcnow()
            user.email_verified = True
            user.email_verified_at = now
            db.session.commit()

            # Reload and verify
            user = User.query.filter_by(email="verify_test@example.com").first()
            assert user.email_verified is True
            assert user.email_verified_at is not None

            # Clean up
            db.session.delete(user)
            db.session.commit()

    def test_email_verification_serialized(self, app):
        """Test that email verification fields are serialized"""
        with app.app_context():
            user = User(
                email="verify_serialize@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Verify Serialize",
                role="USER",
                country="Test",
                institution="Test",
            )
            db.session.add(user)
            db.session.commit()

            serialized = user.serialize()
            assert "email_verified" in serialized
            assert "email_verified_at" in serialized
            assert serialized["email_verified"] is False
            assert serialized["email_verified_at"] is None

            # Clean up
            db.session.delete(user)
            db.session.commit()


@pytest.mark.integration
class TestUserCleanupTasks:
    """Test user cleanup Celery tasks"""

    @pytest.fixture
    def cleanup_env(self):
        """Set up environment variables for cleanup tests"""
        # Use very short cleanup periods for testing
        original_unverified = os.environ.get("UNVERIFIED_USER_CLEANUP_DAYS")
        original_inactive = os.environ.get("INACTIVE_USER_CLEANUP_DAYS")

        # Set test values - 1 day cleanup periods
        os.environ["UNVERIFIED_USER_CLEANUP_DAYS"] = "1"
        os.environ["INACTIVE_USER_CLEANUP_DAYS"] = "1"

        yield

        # Restore original values
        if original_unverified:
            os.environ["UNVERIFIED_USER_CLEANUP_DAYS"] = original_unverified
        else:
            os.environ.pop("UNVERIFIED_USER_CLEANUP_DAYS", None)

        if original_inactive:
            os.environ["INACTIVE_USER_CLEANUP_DAYS"] = original_inactive
        else:
            os.environ.pop("INACTIVE_USER_CLEANUP_DAYS", None)

    def test_cleanup_unverified_users_deletes_eligible_users(self, app, cleanup_env):
        """Test that unverified users with old accounts are deleted"""
        with app.app_context():
            # Create an unverified user with old created_at date
            old_date = datetime.datetime.utcnow() - datetime.timedelta(days=10)
            user = User(
                email="unverified_old@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Unverified Old",
                role="USER",
                country="Test",
                institution="Test",
            )
            user.created_at = old_date
            user.email_verified = False
            user.last_login_at = None
            db.session.add(user)
            db.session.commit()
            user_id = user.id

            # Verify user exists
            assert User.query.get(user_id) is not None

            # Run cleanup task
            from gefapi.tasks.user_cleanup import cleanup_unverified_users

            # Call the task function directly (not through Celery)
            with patch.object(cleanup_unverified_users, "retry"):
                result = cleanup_unverified_users()

            # User should be deleted
            assert User.query.get(user_id) is None
            assert result["deleted_count"] >= 1

    def test_cleanup_never_logged_in_users_deletes_eligible_users(
        self, app, cleanup_env
    ):
        """Test that users who never logged in are deleted after threshold"""
        with app.app_context():
            # Create a user who never logged in
            old_date = datetime.datetime.utcnow() - datetime.timedelta(days=10)
            user = User(
                email="never_logged_in@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Never Logged In",
                role="USER",
                country="Test",
                institution="Test",
            )
            user.created_at = old_date
            user.last_login_at = None
            db.session.add(user)
            db.session.commit()
            user_id = user.id

            # Run cleanup task
            from gefapi.tasks.user_cleanup import cleanup_never_logged_in_users

            with patch.object(cleanup_never_logged_in_users, "retry"):
                result = cleanup_never_logged_in_users()

            # User should be deleted
            assert User.query.get(user_id) is None
            assert result["deleted_count"] >= 1

    def test_cleanup_does_not_delete_users_who_logged_in(self, app, cleanup_env):
        """Test that users with last_login_at are NOT deleted"""
        with app.app_context():
            # Create a user who has logged in
            old_date = datetime.datetime.utcnow() - datetime.timedelta(days=10)
            user = User(
                email="has_logged_in@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Has Logged In",
                role="USER",
                country="Test",
                institution="Test",
            )
            user.created_at = old_date
            user.last_login_at = datetime.datetime.utcnow() - datetime.timedelta(days=5)
            user.email_verified = False  # Even if unverified
            db.session.add(user)
            db.session.commit()
            user_id = user.id

            # Run cleanup tasks
            from gefapi.tasks.user_cleanup import (
                cleanup_never_logged_in_users,
                cleanup_unverified_users,
            )

            with patch.object(cleanup_unverified_users, "retry"):
                cleanup_unverified_users()

            with patch.object(cleanup_never_logged_in_users, "retry"):
                cleanup_never_logged_in_users()

            # User should still exist (they logged in)
            assert User.query.get(user_id) is not None

            # Clean up
            user = User.query.get(user_id)
            if user:
                db.session.delete(user)
                db.session.commit()

    def test_cleanup_does_not_delete_verified_users(self, app, cleanup_env):
        """Test that verified users are NOT deleted even if never logged in"""
        with app.app_context():
            # Create a verified user who never logged in
            old_date = datetime.datetime.utcnow() - datetime.timedelta(days=10)
            user = User(
                email="verified_no_login@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Verified No Login",
                role="USER",
                country="Test",
                institution="Test",
            )
            user.created_at = old_date
            user.email_verified = True
            user.email_verified_at = old_date
            user.last_login_at = None
            db.session.add(user)
            db.session.commit()
            user_id = user.id

            # Run unverified cleanup (should not delete this user)
            from gefapi.tasks.user_cleanup import cleanup_unverified_users

            with patch.object(cleanup_unverified_users, "retry"):
                cleanup_unverified_users()

            # User should still exist (they are verified)
            assert User.query.get(user_id) is not None

            # Clean up
            user = User.query.get(user_id)
            if user:
                db.session.delete(user)
                db.session.commit()


@pytest.mark.integration
class TestLegacyUserProtection:
    """Test that users with NULL email_verified are protected from cleanup.

    The cleanup_unverified_users task explicitly checks for email_verified=False,
    so users with NULL (unlikely in practice after migration) are not deleted.
    """

    @pytest.fixture
    def legacy_env(self):
        """Set environment for legacy user testing"""
        original_unverified = os.environ.get("UNVERIFIED_USER_CLEANUP_DAYS")
        original_inactive = os.environ.get("INACTIVE_USER_CLEANUP_DAYS")

        # Set very short cleanup periods
        os.environ["UNVERIFIED_USER_CLEANUP_DAYS"] = "1"
        os.environ["INACTIVE_USER_CLEANUP_DAYS"] = "1"

        yield

        # Restore
        if original_unverified:
            os.environ["UNVERIFIED_USER_CLEANUP_DAYS"] = original_unverified
        else:
            os.environ.pop("UNVERIFIED_USER_CLEANUP_DAYS", None)

        if original_inactive:
            os.environ["INACTIVE_USER_CLEANUP_DAYS"] = original_inactive
        else:
            os.environ.pop("INACTIVE_USER_CLEANUP_DAYS", None)

    def test_user_with_null_email_verified_not_deleted(self, app, legacy_env):
        """Test users with NULL email_verified are NOT deleted by unverified cleanup"""
        with app.app_context():
            # Create a user with NULL email_verified (edge case)
            old_date = datetime.datetime.utcnow() - datetime.timedelta(days=100)
            user = User(
                email="null_verified_user@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Null Verified User",
                role="USER",
                country="Test",
                institution="Test",
            )
            user.created_at = old_date
            # Simulate edge case with NULL email_verified
            user.email_verified = None
            user.last_login_at = None
            db.session.add(user)
            db.session.commit()
            user_id = user.id

            # Run unverified cleanup task (checks for email_verified=False)
            from gefapi.tasks.user_cleanup import cleanup_unverified_users

            with patch.object(cleanup_unverified_users, "retry"):
                cleanup_unverified_users()

            # User should still exist (email_verified is NULL, not False)
            assert User.query.get(user_id) is not None

            # Clean up
            user = User.query.get(user_id)
            if user:
                db.session.delete(user)
                db.session.commit()

    def test_verified_old_users_not_deleted(self, app, legacy_env):
        """Test verified users are protected regardless of account age"""
        with app.app_context():
            # Create old verified user
            very_old_date = datetime.datetime.utcnow() - datetime.timedelta(days=1000)
            user = User(
                email="very_old_verified@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Very Old Verified",
                role="USER",
                country="Test",
                institution="Test",
            )
            user.created_at = very_old_date
            user.email_verified = True
            user.email_verified_at = very_old_date
            user.last_login_at = None
            db.session.add(user)
            db.session.commit()
            user_id = user.id

            # Run unverified cleanup
            from gefapi.tasks.user_cleanup import cleanup_unverified_users

            with patch.object(cleanup_unverified_users, "retry"):
                cleanup_unverified_users()

            # User should still exist
            assert User.query.get(user_id) is not None

            # Clean up
            user = User.query.get(user_id)
            if user:
                db.session.delete(user)
                db.session.commit()


@pytest.mark.integration
class TestUserCleanupStats:
    """Test the get_user_cleanup_stats task"""

    def test_stats_task_returns_expected_fields(self, app):
        """Test that stats task returns all expected fields"""
        with app.app_context():
            from gefapi.tasks.user_cleanup import get_user_cleanup_stats

            with patch.object(get_user_cleanup_stats, "retry"):
                result = get_user_cleanup_stats()

            # Check all expected fields are present
            assert "total_users" in result
            assert "verified_users" in result
            assert "unverified_users" in result
            assert "logged_in_users" in result
            assert "unverified_eligible_for_cleanup" in result
            assert "never_logged_in_eligible_for_cleanup" in result
            assert "unverified_cleanup_days" in result
            assert "inactive_cleanup_days" in result

    def test_stats_counts_are_accurate(self, app):
        """Test that stats correctly count different user categories"""
        with app.app_context():
            # Get initial counts
            from gefapi.tasks.user_cleanup import get_user_cleanup_stats

            with patch.object(get_user_cleanup_stats, "retry"):
                initial_stats = get_user_cleanup_stats()

            initial_total = initial_stats["total_users"]
            initial_verified = initial_stats["verified_users"]
            initial_logged_in = initial_stats["logged_in_users"]

            # Create a new verified user who has logged in
            user = User(
                email="stats_test@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Stats Test",
                role="USER",
                country="Test",
                institution="Test",
            )
            user.email_verified = True
            user.email_verified_at = datetime.datetime.utcnow()
            user.last_login_at = datetime.datetime.utcnow()
            db.session.add(user)
            db.session.commit()

            # Get new counts
            with patch.object(get_user_cleanup_stats, "retry"):
                new_stats = get_user_cleanup_stats()

            assert new_stats["total_users"] == initial_total + 1
            assert new_stats["verified_users"] == initial_verified + 1
            assert new_stats["logged_in_users"] == initial_logged_in + 1

            # Clean up
            db.session.delete(user)
            db.session.commit()


@pytest.mark.integration
class TestInactiveTokenCleanup:
    """Test inactive refresh token cleanup"""

    def test_inactive_tokens_are_revoked(self, app):
        """Test that tokens not used recently are revoked"""
        with app.app_context():
            from gefapi.models.refresh_token import RefreshToken
            from gefapi.services.refresh_token_service import RefreshTokenService

            # Create a test user
            user = User(
                email="token_test@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Token Test",
                role="USER",
                country="Test",
                institution="Test",
            )
            db.session.add(user)
            db.session.commit()

            # Create a refresh token with old last_used_at
            token = RefreshTokenService.create_refresh_token(user.id)
            token.last_used_at = datetime.datetime.utcnow() - datetime.timedelta(
                days=30
            )
            db.session.commit()
            token_id = token.id

            # Set short inactive period for testing
            original_days = os.environ.get("INACTIVE_TOKEN_DAYS")
            os.environ["INACTIVE_TOKEN_DAYS"] = "1"

            try:
                from gefapi.tasks.refresh_token_cleanup import (
                    cleanup_inactive_refresh_tokens,
                )

                with patch.object(cleanup_inactive_refresh_tokens, "retry"):
                    result = cleanup_inactive_refresh_tokens()

                # Token should be revoked
                token = RefreshToken.query.get(token_id)
                assert token.is_revoked is True
                assert result["revoked_count"] >= 1
            finally:
                # Restore
                if original_days:
                    os.environ["INACTIVE_TOKEN_DAYS"] = original_days
                else:
                    os.environ.pop("INACTIVE_TOKEN_DAYS", None)

                # Clean up
                db.session.delete(user)
                db.session.commit()

    def test_recently_used_tokens_not_revoked(self, app):
        """Test that recently used tokens are NOT revoked"""
        with app.app_context():
            from gefapi.models.refresh_token import RefreshToken
            from gefapi.services.refresh_token_service import RefreshTokenService

            # Create a test user
            user = User(
                email="recent_token@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Recent Token",
                role="USER",
                country="Test",
                institution="Test",
            )
            db.session.add(user)
            db.session.commit()

            # Create a refresh token with recent last_used_at
            token = RefreshTokenService.create_refresh_token(user.id)
            token.last_used_at = datetime.datetime.utcnow()
            db.session.commit()
            token_id = token.id

            # Set inactive period
            original_days = os.environ.get("INACTIVE_TOKEN_DAYS")
            os.environ["INACTIVE_TOKEN_DAYS"] = "14"

            try:
                from gefapi.tasks.refresh_token_cleanup import (
                    cleanup_inactive_refresh_tokens,
                )

                with patch.object(cleanup_inactive_refresh_tokens, "retry"):
                    cleanup_inactive_refresh_tokens()

                # Token should NOT be revoked
                token = RefreshToken.query.get(token_id)
                assert token.is_revoked is False
            finally:
                # Restore
                if original_days:
                    os.environ["INACTIVE_TOKEN_DAYS"] = original_days
                else:
                    os.environ.pop("INACTIVE_TOKEN_DAYS", None)

                # Clean up
                db.session.delete(user)
                db.session.commit()

    def test_null_last_used_tokens_not_revoked(self, app):
        """Test that tokens with NULL last_used_at (legacy) are NOT revoked"""
        with app.app_context():
            from gefapi.models.refresh_token import RefreshToken
            from gefapi.services.refresh_token_service import RefreshTokenService

            # Create a test user
            user = User(
                email="legacy_token@example.com",
                password=STRONG_GENERIC_PASSWORD,
                name="Legacy Token",
                role="USER",
                country="Test",
                institution="Test",
            )
            db.session.add(user)
            db.session.commit()

            # Create a refresh token with NULL last_used_at (legacy)
            token = RefreshTokenService.create_refresh_token(user.id)
            token.last_used_at = None  # Simulate legacy token
            db.session.commit()
            token_id = token.id

            # Set very short inactive period
            original_days = os.environ.get("INACTIVE_TOKEN_DAYS")
            os.environ["INACTIVE_TOKEN_DAYS"] = "1"

            try:
                from gefapi.tasks.refresh_token_cleanup import (
                    cleanup_inactive_refresh_tokens,
                )

                with patch.object(cleanup_inactive_refresh_tokens, "retry"):
                    cleanup_inactive_refresh_tokens()

                # Token should NOT be revoked (NULL last_used_at = legacy)
                token = RefreshToken.query.get(token_id)
                assert token.is_revoked is False
            finally:
                # Restore
                if original_days:
                    os.environ["INACTIVE_TOKEN_DAYS"] = original_days
                else:
                    os.environ.pop("INACTIVE_TOKEN_DAYS", None)

                # Clean up
                db.session.delete(user)
                db.session.commit()
