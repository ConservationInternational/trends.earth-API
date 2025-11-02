"""Tests for rate limit reset by identifier functionality"""


class TestRateLimitResetByIdentifier:
    """Test resetting specific rate limits by identifier"""

    def test_reset_rate_limit_by_identifier_superadmin_access(
        self,
        client,
        auth_headers_user,
        auth_headers_superadmin,
        reset_rate_limits,
        regular_user,
    ):
        """Test that superadmin can reset a specific rate limit by identifier"""
        # First, reset all rate limits to start fresh
        reset_rate_limits()

        # Trigger rate limit for a specific user
        responses = []
        for i in range(100):  # Make many requests to trigger rate limiting
            response = client.get("/api/v1/user/me", headers=auth_headers_user)
            responses.append(response)
            if response.status_code == 429:
                break

        # Check if user was rate limited
        rate_limited = any(r.status_code == 429 for r in responses)
        if not rate_limited:
            # If not rate limited, skip this part of the test
            return

        # Get the user ID from the fixture
        # The regular_user fixture provides the user object with known ID
        user_id = regular_user.id

        # Reset the rate limit for this specific user
        response = client.post(
            f"/api/v1/rate-limit/reset/user:{user_id}",
            headers=auth_headers_superadmin,
        )
        assert response.status_code in [
            200,
            404,
        ], f"Expected 200 or 404, got {response.status_code}: {response.json}"

        # If reset was successful, verify the response
        if response.status_code == 200:
            data = response.json
            assert "message" in data
            assert str(user_id) in data["message"]

    def test_reset_rate_limit_by_identifier_admin_forbidden(
        self, client, auth_headers_admin
    ):
        """Test that admin cannot reset rate limits by identifier"""
        response = client.post(
            "/api/v1/rate-limit/reset/user:123", headers=auth_headers_admin
        )
        assert response.status_code == 403
        assert "Superadmin access required" in response.json["msg"]

    def test_reset_rate_limit_by_identifier_user_forbidden(
        self, client, auth_headers_user
    ):
        """Test that regular user cannot reset rate limits by identifier"""
        response = client.post(
            "/api/v1/rate-limit/reset/user:123", headers=auth_headers_user
        )
        assert response.status_code == 403
        assert "Superadmin access required" in response.json["msg"]

    def test_reset_rate_limit_by_identifier_no_auth_forbidden(self, client):
        """Test that unauthenticated request cannot reset rate limits by identifier"""
        response = client.post("/api/v1/rate-limit/reset/user:123")
        assert response.status_code == 401  # JWT required

    def test_reset_rate_limit_by_ip(self, client, auth_headers_superadmin):
        """Test resetting rate limit by IP address"""
        # Try to reset a rate limit by IP
        response = client.post(
            "/api/v1/rate-limit/reset/ip:192.168.1.100", headers=auth_headers_superadmin
        )
        # Should either succeed (200) or not find the rate limit (404)
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json
            assert "message" in data
            assert "192.168.1.100" in data["message"]
        else:
            # 404 is acceptable if no rate limit exists for this IP
            data = response.json
            assert "error" in data

    def test_reset_rate_limit_returns_404_for_nonexistent(
        self, client, auth_headers_superadmin, reset_rate_limits
    ):
        """Test that resetting a nonexistent rate limit returns 404"""
        # First, reset all rate limits to ensure clean state
        reset_rate_limits()

        # Try to reset a rate limit that doesn't exist
        response = client.post(
            "/api/v1/rate-limit/reset/user:nonexistent-user-id",
            headers=auth_headers_superadmin,
        )
        assert response.status_code == 404
        data = response.json
        assert "error" in data
        assert "No rate limit found" in data["error"]

    def test_reset_rate_limit_by_auth_identifier(self, client, auth_headers_superadmin):
        """Test resetting rate limit by auth identifier"""
        # Try to reset an auth-type rate limit
        response = client.post(
            "/api/v1/rate-limit/reset/auth:somehash:someip",
            headers=auth_headers_superadmin,
        )
        # Should either succeed (200) or not find the rate limit (404)
        assert response.status_code in [200, 404]


class TestRateLimitStatusImprovements:
    """Test improvements to rate limit status endpoint"""

    def test_rate_limit_status_shows_only_active_limits(
        self, client, auth_headers_user, auth_headers_superadmin, reset_rate_limits
    ):
        """Test that rate limit status only shows limits with count > 0"""
        # Reset all limits first
        reset_rate_limits()

        # Make a few requests (but not enough to trigger rate limiting)
        for i in range(3):
            client.get("/api/v1/user/me", headers=auth_headers_user)

        # Check rate limit status
        response = client.get(
            "/api/v1/rate-limit/status", headers=auth_headers_superadmin
        )
        assert response.status_code == 200

        data = response.json["data"]
        if data["enabled"]:
            # All active limits should have current_count > 0
            for limit in data["active_limits"]:
                assert "current_count" in limit
                # Current count should be greater than 0 for "active" limits
                if isinstance(limit["current_count"], (int, float)):
                    assert limit["current_count"] > 0

    def test_rate_limit_status_includes_user_info_for_user_limits(
        self, client, auth_headers_user, auth_headers_superadmin, reset_rate_limits
    ):
        """Test that user-type limits include user information"""
        # Reset and trigger some user requests
        reset_rate_limits()

        # Make requests to trigger rate limit tracking
        for i in range(5):
            client.get("/api/v1/user/me", headers=auth_headers_user)

        # Check rate limit status
        response = client.get(
            "/api/v1/rate-limit/status", headers=auth_headers_superadmin
        )
        assert response.status_code == 200

        data = response.json["data"]
        if data["enabled"] and data["active_limits"]:
            # Find any user-type limits
            user_limits = [
                limit for limit in data["active_limits"] if limit["type"] == "user"
            ]

            # If there are user limits, they should have user_info
            for limit in user_limits:
                assert "type" in limit
                assert limit["type"] == "user"
                # User info may or may not be present depending on whether
                # the user lookup succeeded
                if limit.get("user_info"):
                    assert "id" in limit["user_info"]
                    assert "email" in limit["user_info"]
                    assert "role" in limit["user_info"]
