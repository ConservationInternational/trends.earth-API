"""Tests for account lockout functionality"""

import datetime

import pytest

from gefapi import db
from gefapi.models import User

# Test password for regular user (must match conftest.py)
USER_TEST_PASSWORD = "UserPass123!"


@pytest.fixture
def lockout_test_user(app_no_rate_limiting):
    """Create a test user for lockout testing (uses app without rate limiting)"""
    with app_no_rate_limiting.app_context():
        # Check if user already exists
        existing_user = User.query.filter_by(email="lockout_test@test.com").first()
        if existing_user:
            db.session.delete(existing_user)
            db.session.commit()

        user = User(
            email="lockout_test@test.com",
            password=USER_TEST_PASSWORD,
            name="Lockout Test User",
            role="USER",
            country="Test Country",
            institution="Test Institution",
        )
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)
        return user


class TestAccountLockout:
    """Test account lockout after failed login attempts"""

    def test_user_starts_unlocked(self, app_no_rate_limiting, lockout_test_user):
        """Test that new users are not locked"""
        with app_no_rate_limiting.app_context():
            user = User.query.filter_by(email="lockout_test@test.com").first()
            assert user is not None
            assert user.failed_login_count == 0
            assert user.locked_until is None
            assert not user.is_locked()

    def test_failed_login_increments_counter(
        self, app_no_rate_limiting, lockout_test_user
    ):
        """Test that failed logins increment the counter"""
        client = app_no_rate_limiting.test_client()
        # Try to login with wrong password
        response = client.post(
            "/auth",
            json={"email": "lockout_test@test.com", "password": "wrongpassword"},
        )
        assert response.status_code == 401

        # Check counter was incremented
        with app_no_rate_limiting.app_context():
            user = User.query.filter_by(email="lockout_test@test.com").first()
            assert user.failed_login_count == 1
            assert not user.is_locked()

    def test_successful_login_clears_counter(
        self, app_no_rate_limiting, lockout_test_user
    ):
        """Test that successful login clears the failed login counter"""
        client = app_no_rate_limiting.test_client()

        # First, fail a few logins
        for _ in range(3):
            client.post(
                "/auth",
                json={"email": "lockout_test@test.com", "password": "wrongpassword"},
            )

        # Verify counter was incremented
        with app_no_rate_limiting.app_context():
            user = User.query.filter_by(email="lockout_test@test.com").first()
            assert user.failed_login_count == 3

        # Now login successfully
        response = client.post(
            "/auth",
            json={
                "email": "lockout_test@test.com",
                "password": USER_TEST_PASSWORD,
            },
        )
        assert response.status_code == 200

        # Counter should be cleared
        with app_no_rate_limiting.app_context():
            user = User.query.filter_by(email="lockout_test@test.com").first()
            assert user.failed_login_count == 0
            assert user.locked_until is None

    def test_lockout_after_5_failures(self, app_no_rate_limiting, lockout_test_user):
        """Test that account is locked after 5 failed attempts"""
        client = app_no_rate_limiting.test_client()

        # Fail 5 logins
        for i in range(5):
            response = client.post(
                "/auth",
                json={"email": "lockout_test@test.com", "password": "wrongpassword"},
            )
            assert response.status_code == 401

        # Check user is now locked
        with app_no_rate_limiting.app_context():
            user = User.query.filter_by(email="lockout_test@test.com").first()
            assert user.failed_login_count == 5
            assert user.locked_until is not None
            assert user.is_locked()

        # Even correct password should fail while locked
        response = client.post(
            "/auth",
            json={
                "email": "lockout_test@test.com",
                "password": USER_TEST_PASSWORD,
            },
        )
        assert response.status_code == 401

    def test_locked_user_cannot_login_with_correct_password(
        self, app_no_rate_limiting, lockout_test_user
    ):
        """Test that a locked user cannot login even with correct password"""
        client = app_no_rate_limiting.test_client()
        with app_no_rate_limiting.app_context():
            # Manually lock the user
            user = User.query.filter_by(email="lockout_test@test.com").first()
            user.failed_login_count = 10
            user.locked_until = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
            db.session.commit()

        # Try to login with correct password
        response = client.post(
            "/auth",
            json={
                "email": "lockout_test@test.com",
                "password": USER_TEST_PASSWORD,
            },
        )
        assert response.status_code == 401

    def test_password_reset_clears_lockout(
        self, app_no_rate_limiting, lockout_test_user
    ):
        """Test that password reset clears the lockout"""
        with app_no_rate_limiting.app_context():
            # Manually lock the user
            user = User.query.filter_by(email="lockout_test@test.com").first()
            user.failed_login_count = 20
            user.locked_until = datetime.datetime.utcnow() + datetime.timedelta(
                days=365
            )
            db.session.commit()

            assert user.is_locked()

            # Clear failed logins (simulating password reset)
            user.clear_failed_logins()
            db.session.commit()

            assert not user.is_locked()
            assert user.failed_login_count == 0
            assert user.locked_until is None

    def test_lockout_response_includes_error_code(
        self, app_no_rate_limiting, lockout_test_user
    ):
        """Test that lockout response includes proper error_code for client handling"""
        client = app_no_rate_limiting.test_client()

        # Fail 5 logins to trigger lockout
        for _ in range(5):
            client.post(
                "/auth",
                json={"email": "lockout_test@test.com", "password": "wrongpassword"},
            )

        # The 5th login should trigger lockout and return error details
        # Try to login again while locked
        response = client.post(
            "/auth",
            json={"email": "lockout_test@test.com", "password": "wrongpassword"},
        )
        assert response.status_code == 401
        data = response.get_json()

        # Verify response includes lockout information for clients
        assert data.get("error_code") == "account_locked"
        assert "message" in data
        assert data.get("minutes_remaining") is not None  # 15 minutes for 5 failures
        assert data.get("requires_password_reset") is False

    def test_permanent_lockout_response_requires_password_reset(
        self, app_no_rate_limiting, lockout_test_user
    ):
        """Test that permanent lockout (20+ failures) requires password reset"""
        with app_no_rate_limiting.app_context():
            # Manually set 20 failures to trigger permanent lockout on next failure
            user = User.query.filter_by(email="lockout_test@test.com").first()
            user.failed_login_count = 19
            db.session.commit()

        client = app_no_rate_limiting.test_client()

        # This should be the 20th failure, triggering permanent lockout
        response = client.post(
            "/auth",
            json={"email": "lockout_test@test.com", "password": "wrongpassword"},
        )
        assert response.status_code == 401
        data = response.get_json()

        # Verify response indicates password reset is required
        assert data.get("error_code") == "account_locked"
        assert "message" in data
        assert data.get("minutes_remaining") is None  # No time limit
        assert data.get("requires_password_reset") is True
