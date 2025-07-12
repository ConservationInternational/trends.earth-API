"""
Test cases for refresh token functionality
"""

from datetime import datetime, timedelta

import pytest

from gefapi import db
from gefapi.models.refresh_token import RefreshToken
from gefapi.services.refresh_token_service import RefreshTokenService


class TestRefreshTokens:
    """Test refresh token functionality"""

    @pytest.fixture(autouse=True)
    def setup_method(self, app, regular_user):
        """Clean up refresh tokens before each test"""
        with app.app_context():
            # Clean up any existing refresh tokens for the test user
            RefreshToken.query.filter_by(user_id=regular_user.id).delete()
            db.session.commit()

    def test_create_refresh_token(self, app, regular_user):
        """Test creating a refresh token"""
        with app.app_context():
            refresh_token = RefreshTokenService.create_refresh_token(
                user_id=regular_user.id, device_info="Test Device"
            )

            assert refresh_token is not None
            assert refresh_token.user_id == regular_user.id
            assert refresh_token.token is not None
            assert len(refresh_token.token) > 20  # Should be a substantial token
            assert refresh_token.is_valid()

    def test_validate_refresh_token(self, app, regular_user):
        """Test validating a refresh token"""
        with app.app_context():
            # Create token
            refresh_token = RefreshTokenService.create_refresh_token(regular_user.id)

            # Validate token
            validated_token, user = RefreshTokenService.validate_refresh_token(
                refresh_token.token
            )

            assert validated_token is not None
            assert user is not None
            assert user.id == regular_user.id

    def test_refresh_access_token(self, app, regular_user):
        """Test refreshing an access token"""
        with app.app_context():
            # Create refresh token
            refresh_token = RefreshTokenService.create_refresh_token(regular_user.id)

            # Refresh access token
            access_token, user = RefreshTokenService.refresh_access_token(
                refresh_token.token
            )

            assert access_token is not None
            assert user is not None
            assert user.id == regular_user.id

    def test_revoke_refresh_token(self, app, regular_user):
        """Test revoking a refresh token"""
        with app.app_context():
            # Create token
            refresh_token = RefreshTokenService.create_refresh_token(regular_user.id)

            # Revoke token
            success = RefreshTokenService.revoke_refresh_token(refresh_token.token)
            assert success

            # Try to use revoked token
            validated_token, user = RefreshTokenService.validate_refresh_token(
                refresh_token.token
            )
            assert validated_token is None
            assert user is None

    def test_revoke_all_user_tokens(self, app, regular_user):
        """Test revoking all tokens for a user"""
        with app.app_context():
            # Create multiple tokens
            token1 = RefreshTokenService.create_refresh_token(regular_user.id)
            token2 = RefreshTokenService.create_refresh_token(regular_user.id)

            # Revoke all
            revoked_count = RefreshTokenService.revoke_all_user_tokens(regular_user.id)
            assert revoked_count == 2

            # Verify tokens are revoked
            validated_token1, _ = RefreshTokenService.validate_refresh_token(
                token1.token
            )
            validated_token2, _ = RefreshTokenService.validate_refresh_token(
                token2.token
            )
            assert validated_token1 is None
            assert validated_token2 is None


class TestRefreshTokenAPI:
    """Test refresh token API endpoints"""

    def test_login_returns_refresh_token(self, client_no_rate_limiting):
        """Test that login returns both access and refresh tokens"""
        response = client_no_rate_limiting.post(
            "/auth", json={"email": "user@test.com", "password": "user123"}
        )

        assert response.status_code == 200
        data = response.get_json()

        assert "access_token" in data
        assert "refresh_token" in data
        assert "user_id" in data
        assert "expires_in" in data

    def test_refresh_token_endpoint(self, client_no_rate_limiting, user_token):
        """Test the refresh token endpoint"""
        # First login to get refresh token
        login_response = client_no_rate_limiting.post(
            "/auth", json={"email": "user@test.com", "password": "user123"}
        )

        refresh_token = login_response.get_json()["refresh_token"]

        # Use refresh token to get new access token
        response = client_no_rate_limiting.post(
            "/auth/refresh", json={"refresh_token": refresh_token}
        )

        assert response.status_code == 200
        data = response.get_json()

        assert "access_token" in data
        assert "user_id" in data
        assert "expires_in" in data

    def test_logout_endpoint(
        self, client_no_rate_limiting, auth_headers_user_no_rate_limiting
    ):
        """Test the logout endpoint"""
        # Login first to get refresh token
        login_response = client_no_rate_limiting.post(
            "/auth", json={"email": "user@test.com", "password": "user123"}
        )

        refresh_token = login_response.get_json()["refresh_token"]

        # Logout
        response = client_no_rate_limiting.post(
            "/auth/logout",
            headers=auth_headers_user_no_rate_limiting,
            json={"refresh_token": refresh_token},
        )

        assert response.status_code == 200

        # Try to use refresh token (should fail)
        refresh_response = client_no_rate_limiting.post(
            "/auth/refresh", json={"refresh_token": refresh_token}
        )

        assert refresh_response.status_code == 401

    def test_logout_all_endpoint(self, client, auth_headers_user):
        """Test the logout from all devices endpoint"""
        response = client.post("/auth/logout-all", headers=auth_headers_user)

        assert response.status_code == 200
        data = response.get_json()
        assert "msg" in data

    def test_get_user_sessions(self, client, auth_headers_user):
        """Test getting user sessions"""
        response = client.get("/api/v1/user/me/sessions", headers=auth_headers_user)

        assert response.status_code == 200
        data = response.get_json()
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_revoke_user_session(self, client, auth_headers_user, regular_user, app):
        """Test revoking a specific user session"""
        with app.app_context():
            # Create a session
            refresh_token = RefreshTokenService.create_refresh_token(regular_user.id)

            response = client.delete(
                f"/api/v1/user/me/sessions/{refresh_token.id}",
                headers=auth_headers_user,
            )

            assert response.status_code == 200

    def test_invalid_refresh_token(self, client):
        """Test using an invalid refresh token"""
        response = client.post("/auth/refresh", json={"refresh_token": "invalid_token"})

        assert response.status_code == 401

    def test_expired_refresh_token(self, client, regular_user, app):
        """Test using an expired refresh token"""
        with app.app_context():
            # Create an expired token
            expired_token = RefreshToken(
                user_id=regular_user.id,
                expires_at=datetime.utcnow() - timedelta(days=1),
            )

            from gefapi import db

            db.session.add(expired_token)
            db.session.commit()

            response = client.post(
                "/auth/refresh", json={"refresh_token": expired_token.token}
            )

            assert response.status_code == 401


class TestRefreshTokenSecurity:
    """Test security aspects of refresh tokens"""

    def test_refresh_token_uniqueness(self, app, regular_user):
        """Test that refresh tokens are unique"""
        with app.app_context():
            token1 = RefreshTokenService.create_refresh_token(regular_user.id)
            token2 = RefreshTokenService.create_refresh_token(regular_user.id)

            assert token1.token != token2.token

    def test_refresh_token_entropy(self, app, regular_user):
        """Test that refresh tokens have sufficient entropy"""
        with app.app_context():
            token = RefreshTokenService.create_refresh_token(regular_user.id)

            # Token should be at least 32 characters (URL-safe base64 encoded)
            assert len(token.token) >= 32

            # Should contain alphanumeric characters
            assert any(c.isalpha() for c in token.token)
            assert any(c.isdigit() for c in token.token)

    def test_device_info_tracking(self, app, regular_user):
        """Test that device information is tracked"""
        with app.app_context():
            device_info = "Mozilla/5.0 Test Browser"
            token = RefreshTokenService.create_refresh_token(
                regular_user.id, device_info=device_info
            )

            assert token.device_info == device_info

    def test_cleanup_expired_tokens(self, app, regular_user):
        """Test cleaning up expired tokens"""
        with app.app_context():
            # Create expired token
            expired_token = RefreshToken(
                user_id=regular_user.id,
                expires_at=datetime.utcnow() - timedelta(days=1),
            )

            from gefapi import db

            db.session.add(expired_token)
            db.session.commit()

            # Run cleanup
            cleaned_count = RefreshTokenService.cleanup_expired_tokens()

            assert cleaned_count >= 1

            # Verify token was deleted
            remaining_token = RefreshToken.query.filter_by(id=expired_token.id).first()
            assert remaining_token is None
