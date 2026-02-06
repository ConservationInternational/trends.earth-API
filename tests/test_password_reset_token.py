"""Tests for password reset token functionality including email verification.

This module tests the password reset flow, particularly ensuring that:
1. Password reset tokens work correctly
2. Users are marked as email verified after successfully using a reset token
3. Already verified users remain verified after password reset
"""

import datetime
from unittest.mock import patch

import pytest

from gefapi import db
from gefapi.models import PasswordResetToken, User
from gefapi.services.user_service import UserService

# Strong password that meets validation requirements
STRONG_PASSWORD = "NewSecure123!"


class TestPasswordResetTokenEmailVerification:
    """Test that password reset tokens properly verify user emails."""

    def test_reset_password_with_token_verifies_unverified_user(self, app):
        """Test that using a password reset token marks an unverified user as verified."""
        with app.app_context():
            # Create an unverified user
            user = User(
                email="unverified@test.com",
                password=STRONG_PASSWORD,
                name="Test User",
                country="US",
                institution="Test Institution",
            )
            # Ensure user is unverified
            user.email_verified = False
            user.email_verified_at = None
            db.session.add(user)
            db.session.commit()

            # Verify user is not verified initially
            assert user.email_verified is False
            assert user.email_verified_at is None

            # Create a password reset token for this user
            reset_token = PasswordResetToken(user_id=user.id)
            db.session.add(reset_token)
            db.session.commit()

            # Get the token string
            token_string = reset_token.token

            # Use the token to reset password
            updated_user = UserService.reset_password_with_token(
                token_string, "AnotherStrong456!"
            )

            # Verify the user is now marked as verified
            assert updated_user.email_verified is True
            assert updated_user.email_verified_at is not None
            # Verify the timestamp is recent (within last minute)
            now = datetime.datetime.utcnow()
            time_diff = now - updated_user.email_verified_at
            assert time_diff.total_seconds() < 60

    def test_reset_password_with_token_preserves_already_verified_user(self, app):
        """Test that password reset on already verified user doesn't change verified status."""
        with app.app_context():
            # Create an already verified user
            user = User(
                email="verified@test.com",
                password=STRONG_PASSWORD,
                name="Verified User",
                country="US",
                institution="Test Institution",
            )
            original_verification_time = datetime.datetime(
                2024, 1, 1, 12, 0, 0
            )
            user.email_verified = True
            user.email_verified_at = original_verification_time
            db.session.add(user)
            db.session.commit()

            # Verify user is already verified
            assert user.email_verified is True
            assert user.email_verified_at == original_verification_time

            # Create a password reset token
            reset_token = PasswordResetToken(user_id=user.id)
            db.session.add(reset_token)
            db.session.commit()

            # Reset password
            updated_user = UserService.reset_password_with_token(
                reset_token.token, "NewPassword789!"
            )

            # Verify the original verification status is preserved
            assert updated_user.email_verified is True
            # Original verification time should be preserved (not updated)
            assert (
                updated_user.email_verified_at
                == original_verification_time
            )

    def test_reset_password_with_invalid_token_fails(self, app):
        """Test that invalid tokens are rejected."""
        with app.app_context():
            from gefapi.errors import UserNotFound

            with pytest.raises(UserNotFound, match="Invalid or expired"):
                UserService.reset_password_with_token(
                    "invalid-token-string", STRONG_PASSWORD
                )

    def test_reset_password_with_expired_token_fails(self, app):
        """Test that expired tokens are rejected."""
        with app.app_context():
            from gefapi.errors import UserNotFound

            # Create a user
            user = User(
                email="expired_token@test.com",
                password=STRONG_PASSWORD,
                name="Test User",
                country="US",
                institution="Test Institution",
            )
            db.session.add(user)
            db.session.commit()

            # Create an expired token
            reset_token = PasswordResetToken(user_id=user.id)
            # Set expiry to the past
            reset_token.expires_at = datetime.datetime.utcnow() - datetime.timedelta(hours=2)
            db.session.add(reset_token)
            db.session.commit()

            with pytest.raises(UserNotFound, match="Invalid or expired"):
                UserService.reset_password_with_token(
                    reset_token.token, STRONG_PASSWORD
                )

    def test_reset_password_with_used_token_fails(self, app):
        """Test that already-used tokens are rejected."""
        with app.app_context():
            from gefapi.errors import UserNotFound

            # Create a user
            user = User(
                email="used_token@test.com",
                password=STRONG_PASSWORD,
                name="Test User",
                country="US",
                institution="Test Institution",
            )
            db.session.add(user)
            db.session.commit()

            # Create a token and mark it as used
            reset_token = PasswordResetToken(user_id=user.id)
            reset_token.used_at = datetime.datetime.utcnow()
            db.session.add(reset_token)
            db.session.commit()

            with pytest.raises(UserNotFound, match="Invalid or expired"):
                UserService.reset_password_with_token(
                    reset_token.token, STRONG_PASSWORD
                )


class TestSecureUserRegistrationFlow:
    """Test the complete secure registration flow with email verification."""

    @patch("gefapi.services.user_service.EmailService.send_html_email")
    def test_secure_registration_creates_unverified_user(self, mock_email, app):
        """Test that secure registration creates user with email_verified=False."""
        with app.app_context():
            user_data = {
                "email": "newuser@test.com",
                "name": "New User",
                "country": "US",
                "institution": "Test Institution",
            }

            # Create user with secure flow (legacy=False)
            user = UserService.create_user(user_data, legacy=False)

            # User should be unverified at creation
            assert user.email_verified is False
            assert user.email_verified_at is None

            # Email should have been sent
            assert mock_email.called

    @patch("gefapi.services.user_service.EmailService.send_html_email")
    def test_secure_registration_complete_flow_verifies_user(self, mock_email, app):
        """Test complete secure registration flow: create user -> use token -> verified."""
        with app.app_context():
            user_data = {
                "email": "complete_flow@test.com",
                "name": "Complete Flow User",
                "country": "US",
                "institution": "Test Institution",
            }

            # Create user with secure flow
            user = UserService.create_user(user_data, legacy=False)

            # User should be unverified
            assert user.email_verified is False

            # Find the password reset token created during registration
            reset_token = PasswordResetToken.query.filter_by(user_id=user.id).first()
            assert reset_token is not None

            # User "clicks link" and sets password
            updated_user = UserService.reset_password_with_token(
                reset_token.token, "UserChosenPass1!"
            )

            # User should now be verified
            assert updated_user.email_verified is True
            assert updated_user.email_verified_at is not None
