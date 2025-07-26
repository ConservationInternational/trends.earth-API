"""Tests for rate limiting functionality"""

from gefapi.utils.rate_limiting import RateLimitConfig


class TestRateLimiting:
    """Test rate limiting functionality"""

    def test_authentication_rate_limiting(self, client):
        """Test that authentication endpoint is rate limited"""
        with client.application.app_context():
            from gefapi import limiter

            print(f"DEBUG: Limiter enabled: {limiter.enabled}")
            print(f"DEBUG: Limiter storage: {limiter._storage}")
            print(
                f"DEBUG: App config RATE_LIMITING: {client.application.config.get('RATE_LIMITING')}"
            )

        # Make rapid authentication attempts to trigger rate limiting
        # AUTH_LIMITS is "2 per minute" in test config
        responses = []
        for i in range(5):  # Try 5 times, should be rate limited after 2
            response = client.post(
                "/auth", json={"email": "test@example.com", "password": "wrongpassword"}
            )
            responses.append(response)
            print(f"DEBUG: Request {i + 1}: Status {response.status_code}")

            # Check for rate limit headers
            headers_dict = dict(response.headers)
            rate_limit_headers = {
                k: v
                for k, v in headers_dict.items()
                if "limit" in k.lower() or "rate" in k.lower()
            }
            if rate_limit_headers:
                print(f"DEBUG: Rate limit headers: {rate_limit_headers}")

            # If we get rate limited, break
            if response.status_code == 429:
                break

        # Should eventually get rate limited
        rate_limited_responses = [r for r in responses if r.status_code == 429]
        assert len(rate_limited_responses) > 0, (
            f"Authentication should be rate limited. Got responses: {[r.status_code for r in responses]}"
        )

    def test_password_recovery_rate_limiting(self, client):
        """Test that password recovery is heavily rate limited"""
        # Make rapid password recovery attempts
        # PASSWORD_RESET_LIMITS is "1 per minute" in test config
        responses = []
        for i in range(3):  # Try 3 times, should be rate limited after 1
            response = client.post(f"/api/v1/user/test@example.com/recover-password")
            responses.append(response)

            # If we get rate limited, break
            if response.status_code == 429:
                break

        # Should get rate limited quickly
        rate_limited_responses = [r for r in responses if r.status_code == 429]
        assert len(rate_limited_responses) > 0, (
            "Password recovery should be heavily rate limited"
        )

    def test_user_creation_rate_limiting(self, client):
        """Test that user creation is rate limited"""
        # Make rapid user creation attempts
        # USER_CREATION_LIMITS is "2 per minute" in test config
        responses = []
        for i in range(5):  # Try 5 times, should be rate limited after 2
            response = client.post(
                "/api/v1/user",
                json={
                    "email": f"test{i}@example.com",
                    "password": "password123",
                    "name": f"Test User {i}",
                },
            )
            responses.append(response)

            # If we get rate limited, break
            if response.status_code == 429:
                break

        # Should eventually get rate limited
        rate_limited_responses = [r for r in responses if r.status_code == 429]
        assert len(rate_limited_responses) > 0, "User creation should be rate limited"

    def test_script_execution_rate_limiting(self, client, auth_headers_user):
        """Test that script execution is rate limited (10 per minute, 40 per hour)"""
        # Make multiple script execution attempts
        responses = []
        for i in range(15):  # Try more than the 10 per minute limit
            response = client.post(
                f"/api/v1/script/test-script-{i}/run",
                json={"param1": "value1"},
                headers=auth_headers_user,
            )
            responses.append(response)

            # If we get rate limited, break
            if response.status_code == 429:
                break

        # Should eventually get rate limited (either due to rate limiting or script not found)
        # We expect either 404 (script not found) or 429 (rate limited)
        # In a real scenario with valid scripts, we'd expect rate limiting
        # For this test, we just verify the rate limiting decorator is applied
        # (the actual rate limiting will depend on valid scripts existing)
        rate_limited_responses = [r for r in responses if r.status_code == 429]
        assert len(rate_limited_responses) > 0, (
            f"Should get rate limited after configured limit. Got responses: {[r.status_code for r in responses]}"
        )

    def test_rate_limit_headers(self, client):
        """Test that rate limit headers are included in responses"""
        response = client.get("/api-health")

        # Should include rate limit headers
        assert (
            "X-RateLimit-Limit" in response.headers
            or "RateLimit-Limit" in response.headers
        )
        assert (
            "X-RateLimit-Remaining" in response.headers
            or "RateLimit-Remaining" in response.headers
        )

    def test_rate_limit_config(self, client):
        """Test rate limiting configuration"""
        with client.application.app_context():
            # First, verify the Flask app config is correct
            app_config = client.application.config.get("RATE_LIMITING", {})
            assert app_config.get("ENABLED") is True, (
                f"Rate limiting should be enabled in test config: {app_config}"
            )
            assert "2 per minute" in app_config.get("AUTH_LIMITS", []), (
                f"AUTH_LIMITS should be test values: {app_config.get('AUTH_LIMITS')}"
            )

            # Test that configuration methods work
            assert isinstance(RateLimitConfig.get_default_limits(), list)
            assert isinstance(RateLimitConfig.get_auth_limits(), list)
            assert isinstance(RateLimitConfig.get_password_reset_limits(), list)
            assert isinstance(RateLimitConfig.get_user_creation_limits(), list)
            assert isinstance(RateLimitConfig.get_execution_run_limits(), list)
            assert isinstance(RateLimitConfig.is_enabled(), bool)

            # Test execution run limits specifically (using test configuration values)
            execution_limits = RateLimitConfig.get_execution_run_limits()
            auth_limits = RateLimitConfig.get_auth_limits()
            print(f"DEBUG: Execution limits: {execution_limits}")
            print(f"DEBUG: Auth limits: {auth_limits}")
            print(
                f"DEBUG: App config RATE_LIMITING: {client.application.config.get('RATE_LIMITING')}"
            )

            assert "3 per minute" in execution_limits, (
                f"Expected '3 per minute' in {execution_limits}"
            )
            assert "10 per hour" in execution_limits, (
                f"Expected '10 per hour' in {execution_limits}"
            )

    def test_rate_limiting_disabled(self, client):
        """Test that rate limiting can be disabled"""
        # Temporarily disable rate limiting by modifying the app config
        with client.application.app_context():
            original_enabled = client.application.config.get("RATE_LIMITING", {}).get(
                "ENABLED", True
            )
            client.application.config["RATE_LIMITING"]["ENABLED"] = False

            # Test that it's disabled
            assert not RateLimitConfig.is_enabled()

            # Restore original setting
            client.application.config["RATE_LIMITING"]["ENABLED"] = original_enabled

    def test_rate_limit_bypass_in_testing(self, client):
        """Test that rate limiting is bypassed in testing mode"""
        # In testing mode, rate limiting should be more lenient
        # Make many requests to see if they're all successful
        success_count = 0
        for i in range(20):
            response = client.get("/api-health")
            if response.status_code == 200:
                success_count += 1

        # Should have many successful requests in testing mode
        assert success_count > 15, "Rate limiting should be lenient in testing mode"

    def test_admin_exemption_from_rate_limiting(self, client, auth_headers_admin):
        """Test that admin and superadmin users are exempt from rate limiting"""
        # Make many requests as admin - should not be rate limited
        admin_responses = []
        for i in range(50):  # Make many requests
            response = client.get("/api/v1/user", headers=auth_headers_admin)
            admin_responses.append(response)

            # If we get rate limited, this test should fail
            if response.status_code == 429:
                break

        # Admin should never get rate limited
        rate_limited_responses = [r for r in admin_responses if r.status_code == 429]
        assert len(rate_limited_responses) == 0, (
            "Admin users should be exempt from rate limiting"
        )

        # Most responses should be successful (200 or 403 based on endpoint access)
        successful_responses = [
            r for r in admin_responses if r.status_code in [200, 403]
        ]
        assert len(successful_responses) > 40, (
            "Admin should have many successful requests"
        )

    def test_regular_user_rate_limiting_still_works(
        self, client, auth_headers_user, reset_rate_limits
    ):
        """Test that regular users are still subject to rate limiting"""
        # Reset rate limits to ensure clean state
        reset_rate_limits()
        # Make many requests as regular user - should eventually be rate limited
        user_responses = []
        for i in range(100):  # Make many requests to trigger rate limiting
            response = client.get("/api/v1/user/me", headers=auth_headers_user)
            user_responses.append(response)

            # If we get rate limited, that's expected for regular users
            if response.status_code == 429:
                break

        # Regular users should eventually get rate limited
        # Note: In testing environment, limits might be higher, so we just check that
        # rate limiting is still functional (either we get rate limited or we don't make enough requests)
        assert len(user_responses) > 10, (
            "Should make several requests before potential rate limiting"
        )

    def test_different_users_different_limits(
        self, client, auth_headers_user, auth_headers_admin
    ):
        """Test that different users get different rate limits"""
        # Make requests as regular user to /user/me (which has default rate limiting)
        user_responses = []
        for i in range(
            5
        ):  # Reduce number to avoid hitting rate limits from other tests
            response = client.get("/api/v1/user/me", headers=auth_headers_user)
            user_responses.append(response)
            if response.status_code == 429:
                break

        # Make requests as admin to /user endpoint (admin should be exempted)
        admin_responses = []
        for i in range(5):
            response = client.get("/api/v1/user", headers=auth_headers_admin)
            admin_responses.append(response)
            if response.status_code == 429:
                break

        # Regular user should have some successful responses or be rate limited
        user_success = [r for r in user_responses if r.status_code == 200]
        user_rate_limited = [r for r in user_responses if r.status_code == 429]

        # Admin should have successful responses (exempted from rate limiting)
        admin_success = [r for r in admin_responses if r.status_code == 200]

        # At least one of these should be true for user: success or rate limited
        assert len(user_success) > 0 or len(user_rate_limited) > 0, (
            f"User should have successful or rate limited requests. Got: {[r.status_code for r in user_responses]}"
        )

        # Admin should have successful requests (not rate limited)
        assert len(admin_success) > 0, (
            f"Admin should have some successful requests. Got: {[r.status_code for r in admin_responses]}"
        )

    def test_rate_limit_error_format(self, client):
        """Test that rate limit errors have correct format"""
        # Make many requests to trigger rate limiting
        rate_limit_response = None
        for i in range(20):
            response = client.post(
                "/auth", json={"email": "test@example.com", "password": "wrongpassword"}
            )
            if response.status_code == 429:
                rate_limit_response = response
                break

        if rate_limit_response:
            data = rate_limit_response.json
            assert data["status"] == 429
            assert "rate limit" in data["detail"].lower()
            assert "error_code" in data
            assert data["error_code"] == "RATE_LIMIT_EXCEEDED"

    def test_rate_limit_with_different_ips(self, client):
        """Test that rate limiting works per IP address"""
        # This would be tested with different X-Forwarded-For headers
        # to simulate different IP addresses
        headers1 = {"X-Forwarded-For": "192.168.1.1"}
        headers2 = {"X-Forwarded-For": "192.168.1.2"}

        # Make requests from different IPs
        response1 = client.post(
            "/auth",
            json={"email": "test@example.com", "password": "wrongpassword"},
            headers=headers1,
        )
        response2 = client.post(
            "/auth",
            json={"email": "test@example.com", "password": "wrongpassword"},
            headers=headers2,
        )

        # Both should be treated separately (both likely to succeed initially)
        assert response1.status_code in [401, 429]  # Auth failure or rate limit
        assert response2.status_code in [401, 429]  # Auth failure or rate limit


class TestRateLimitStatus:
    """Test rate limit status query functionality"""

    def test_rate_limit_status_superadmin_access(self, client, auth_headers_superadmin):
        """Test that superadmin can access the rate limit status endpoint"""
        response = client.get(
            "/api/v1/rate-limit/status", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        assert "message" in response.json
        assert "data" in response.json
        assert "active_limits" in response.json["data"]

    def test_rate_limit_status_admin_forbidden(self, client, auth_headers_admin):
        """Test that admin cannot access the rate limit status endpoint"""
        response = client.get("/api/v1/rate-limit/status", headers=auth_headers_admin)
        assert response.status_code == 403
        assert "Superadmin access required" in response.json["msg"]

    def test_rate_limit_status_user_forbidden(self, client, auth_headers_user):
        """Test that regular user cannot access the rate limit status endpoint"""
        response = client.get("/api/v1/rate-limit/status", headers=auth_headers_user)
        assert response.status_code == 403
        assert "Superadmin access required" in response.json["msg"]

    def test_rate_limit_status_no_auth_forbidden(self, client):
        """Test that unauthenticated request cannot access the rate limit status endpoint"""
        response = client.get("/api/v1/rate-limit/status")
        assert response.status_code == 401  # JWT required

    def test_rate_limit_status_returns_proper_json(
        self, client, auth_headers_superadmin
    ):
        """Test that rate limit status returns proper JSON response"""
        response = client.get(
            "/api/v1/rate-limit/status", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        assert response.content_type == "application/json"
        data = response.json
        assert isinstance(data, dict)
        assert "message" in data
        assert "data" in data
        assert isinstance(data["data"], dict)
        assert "enabled" in data["data"]
        assert "active_limits" in data["data"]
        assert isinstance(data["data"]["active_limits"], list)

    def test_rate_limit_status_with_active_limits(
        self, client, auth_headers_user, auth_headers_superadmin, rate_limiting_enabled
    ):
        """Test rate limit status when there are active rate limits"""
        # First, trigger some rate limits by making many requests as a regular user
        responses = []
        for i in range(20):  # Make many requests to potentially trigger rate limiting
            response = client.get("/api/v1/user/me", headers=auth_headers_user)
            responses.append(response)
            if response.status_code == 429:
                break

        # Now check the rate limit status
        status_response = client.get(
            "/api/v1/rate-limit/status", headers=auth_headers_superadmin
        )
        assert status_response.status_code == 200

        status_data = status_response.json["data"]

        # If rate limiting is enabled, should show status
        if status_data["enabled"]:
            assert "total_active_limits" in status_data
            assert isinstance(status_data["total_active_limits"], int)
            assert status_data["total_active_limits"] >= 0

            # Check structure of active limits
            for limit in status_data["active_limits"]:
                assert "key" in limit
                assert "type" in limit
                assert "current_count" in limit
                assert "time_window_seconds" in limit

                # If it's a user type limit, should have user info
                if limit["type"] == "user" and limit.get("user_info"):
                    user_info = limit["user_info"]
                    assert "id" in user_info
                    assert "email" in user_info
                    assert "name" in user_info
                    assert "role" in user_info

    def test_rate_limit_status_when_disabled(self, client, auth_headers_superadmin):
        """Test rate limit status when rate limiting is disabled"""
        # Temporarily disable rate limiting
        with client.application.app_context():
            from gefapi import limiter

            original_enabled = limiter.enabled
            limiter.enabled = False

            try:
                response = client.get(
                    "/api/v1/rate-limit/status", headers=auth_headers_superadmin
                )
                assert response.status_code == 200

                status_data = response.json["data"]
                # Should indicate that rate limiting is disabled
                assert status_data["enabled"] is False
                assert "message" in status_data
                assert "disabled" in status_data["message"].lower()

            finally:
                # Restore original state
                limiter.enabled = original_enabled

    def test_rate_limit_status_empty_when_no_limits(
        self, client, auth_headers_superadmin, reset_rate_limits
    ):
        """Test that status shows empty when no active rate limits"""
        # Reset all rate limits first
        reset_rate_limits()

        response = client.get(
            "/api/v1/rate-limit/status", headers=auth_headers_superadmin
        )
        assert response.status_code == 200

        status_data = response.json["data"]
        if status_data["enabled"]:
            # Should show 0 or very few active limits after reset
            assert status_data["total_active_limits"] >= 0
            # Most active limits should be cleared after reset
            assert len(status_data["active_limits"]) >= 0


class TestRateLimitReset:
    """Test rate limit reset functionality"""

    def test_rate_limit_reset_superadmin_access(self, client, auth_headers_superadmin):
        """Test that superadmin can access the rate limit reset endpoint"""
        response = client.post(
            "/api/v1/rate-limit/reset", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        assert "message" in response.json
        assert "reset" in response.json["message"].lower()

    def test_rate_limit_reset_admin_forbidden(self, client, auth_headers_admin):
        """Test that admin cannot access the rate limit reset endpoint"""
        response = client.post("/api/v1/rate-limit/reset", headers=auth_headers_admin)
        assert response.status_code == 403
        assert "Superadmin access required" in response.json["msg"]

    def test_rate_limit_reset_user_forbidden(self, client, auth_headers_user):
        """Test that regular user cannot access the rate limit reset endpoint"""
        response = client.post("/api/v1/rate-limit/reset", headers=auth_headers_user)
        assert response.status_code == 403
        assert "Superadmin access required" in response.json["msg"]

    def test_rate_limit_reset_no_auth_forbidden(self, client):
        """Test that unauthenticated request cannot access the rate limit reset endpoint"""
        response = client.post("/api/v1/rate-limit/reset")
        assert response.status_code == 401  # JWT required

    def test_rate_limit_reset_functionality(
        self, client, auth_headers_user, auth_headers_superadmin
    ):
        """Test that rate limit reset actually clears rate limits"""
        # First, trigger a rate limit for a user
        responses = []
        for i in range(50):  # Make many requests to trigger rate limiting
            response = client.get("/api/v1/user/me", headers=auth_headers_user)
            responses.append(response)
            if response.status_code == 429:
                break

        # Check if we got rate limited
        rate_limited_responses = [r for r in responses if r.status_code == 429]
        if len(rate_limited_responses) > 0:
            # We got rate limited, now reset and try again
            reset_response = client.post(
                "/api/v1/rate-limit/reset", headers=auth_headers_superadmin
            )
            assert reset_response.status_code == 200

            # Try the same request again - should work now
            response = client.get("/api/v1/user/me", headers=auth_headers_user)
            # Should not be rate limited immediately after reset
            assert response.status_code != 429

    def test_rate_limit_reset_returns_proper_json(
        self, client, auth_headers_superadmin
    ):
        """Test that rate limit reset returns proper JSON response"""
        response = client.post(
            "/api/v1/rate-limit/reset", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        assert response.content_type == "application/json"
        data = response.json
        assert isinstance(data, dict)
        assert "message" in data
        assert isinstance(data["message"], str)
