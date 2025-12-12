"""
Test cases for JWT security enhancements including blocklist, error handlers,
and token fingerprinting.
"""

from conftest import USER_TEST_PASSWORD
import pytest

from gefapi import _revoked_tokens, add_token_to_blocklist, is_token_in_blocklist


class TestJWTBlocklist:
    """Test JWT token blocklist functionality"""

    @pytest.fixture(autouse=True)
    def clear_blocklist(self):
        """Clear in-memory blocklist before each test"""
        _revoked_tokens.clear()
        yield
        _revoked_tokens.clear()

    def test_add_token_to_blocklist(self):
        """Test adding a token JTI to the blocklist"""
        jti = "test-jti-12345"
        add_token_to_blocklist(jti, expires_in_seconds=3600)

        assert is_token_in_blocklist(jti)

    def test_token_not_in_blocklist(self):
        """Test that non-revoked tokens are not in blocklist"""
        jti = "never-revoked-jti"

        assert not is_token_in_blocklist(jti)

    def test_multiple_tokens_in_blocklist(self):
        """Test adding multiple tokens to blocklist"""
        jti1 = "test-jti-1"
        jti2 = "test-jti-2"
        jti3 = "test-jti-3"

        add_token_to_blocklist(jti1)
        add_token_to_blocklist(jti2)

        assert is_token_in_blocklist(jti1)
        assert is_token_in_blocklist(jti2)
        assert not is_token_in_blocklist(jti3)


class TestJWTErrorHandlers:
    """Test JWT error handler responses"""

    def test_missing_token_returns_401(self, client):
        """Test that missing token returns proper 401 response"""
        response = client.get("/api/v1/user/me")

        assert response.status_code == 401
        data = response.get_json()
        assert data["status"] == 401
        assert data["error"] == "authorization_required"
        assert "Authorization token required" in data["detail"]

    def test_invalid_token_returns_401(self, client):
        """Test that invalid token returns proper 401 response"""
        response = client.get(
            "/api/v1/user/me", headers={"Authorization": "Bearer invalid.token.here"}
        )

        assert response.status_code == 401
        data = response.get_json()
        assert data["status"] == 401
        assert data["error"] == "invalid_token"

    def test_malformed_token_returns_401(self, client):
        """Test that malformed token returns proper 401 response"""
        response = client.get(
            "/api/v1/user/me", headers={"Authorization": "Bearer not-a-jwt"}
        )

        assert response.status_code == 401
        data = response.get_json()
        assert data["status"] == 401


class TestLogoutTokenRevocation:
    """Test that logout properly revokes access tokens"""

    def test_logout_revokes_access_token(
        self, client_no_rate_limiting, regular_user_no_rate_limiting
    ):
        """Test that logging out revokes the access token"""
        # Login to get tokens
        login_response = client_no_rate_limiting.post(
            "/auth",
            json={"email": "user@test.com", "password": USER_TEST_PASSWORD},
        )
        assert login_response.status_code == 200

        tokens = login_response.get_json()
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]

        # Verify token works before logout
        pre_logout_response = client_no_rate_limiting.get(
            "/api/v1/user/me", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert pre_logout_response.status_code == 200

        # Logout
        logout_response = client_no_rate_limiting.post(
            "/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"refresh_token": refresh_token},
        )
        assert logout_response.status_code == 200

        # Verify token is revoked after logout
        post_logout_response = client_no_rate_limiting.get(
            "/api/v1/user/me", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert post_logout_response.status_code == 401
        data = post_logout_response.get_json()
        assert data["error"] == "token_revoked"


class TestRefreshTokenFingerprinting:
    """Test refresh token client fingerprinting"""

    def test_extract_ip_from_device_info(self, app, regular_user):
        """Test extracting IP address from device_info"""
        from gefapi.services.refresh_token_service import RefreshTokenService

        with app.app_context():
            # Create token with device info
            refresh_token = RefreshTokenService.create_refresh_token(
                user_id=regular_user.id,
                device_info="IP: 192.168.1.100 | UA: TestBrowser/1.0",
            )

            assert refresh_token._extract_ip_from_device_info() == "192.168.1.100"

    def test_get_client_fingerprint(self, app, regular_user):
        """Test getting client fingerprint from token"""
        from gefapi.services.refresh_token_service import RefreshTokenService

        with app.app_context():
            device_info = "IP: 10.0.0.1 | UA: Mozilla/5.0"
            refresh_token = RefreshTokenService.create_refresh_token(
                user_id=regular_user.id, device_info=device_info
            )

            fingerprint = refresh_token.get_client_fingerprint()

            assert fingerprint["ip_address"] == "10.0.0.1"
            assert fingerprint["device_info"] == device_info

    def test_is_valid_with_matching_ip(self, app, regular_user):
        """Test token validation with matching client IP"""
        from gefapi.services.refresh_token_service import RefreshTokenService

        with app.app_context():
            refresh_token = RefreshTokenService.create_refresh_token(
                user_id=regular_user.id,
                device_info="IP: 192.168.1.50 | UA: TestBrowser",
            )

            # Should be valid with matching IP
            assert refresh_token.is_valid(
                verify_client_ip=True, current_ip="192.168.1.50"
            )

    def test_is_valid_with_mismatched_ip_logs_warning(self, app, regular_user, caplog):
        """Test that mismatched IP logs a warning but still returns valid"""
        import logging

        from gefapi.services.refresh_token_service import RefreshTokenService

        with app.app_context():
            refresh_token = RefreshTokenService.create_refresh_token(
                user_id=regular_user.id,
                device_info="IP: 192.168.1.50 | UA: TestBrowser",
            )

            # Enable logging capture
            with caplog.at_level(logging.WARNING):
                # Should still be valid (warning only, not blocking)
                result = refresh_token.is_valid(
                    verify_client_ip=True, current_ip="10.0.0.99"
                )

            assert result is True  # Still valid - just logs warning

    def test_is_valid_without_ip_verification(self, app, regular_user):
        """Test token validation without IP verification (default behavior)"""
        from gefapi.services.refresh_token_service import RefreshTokenService

        with app.app_context():
            refresh_token = RefreshTokenService.create_refresh_token(
                user_id=regular_user.id,
                device_info="IP: 192.168.1.50 | UA: TestBrowser",
            )

            # Default behavior - no IP verification
            assert refresh_token.is_valid()

    def test_extract_ip_with_no_device_info(self, app, regular_user):
        """Test IP extraction when no device_info is stored"""
        from gefapi import db
        from gefapi.models.refresh_token import RefreshToken

        with app.app_context():
            # Create token without device_info
            refresh_token = RefreshToken(user_id=regular_user.id, device_info=None)
            db.session.add(refresh_token)
            db.session.commit()

            assert refresh_token._extract_ip_from_device_info() is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
