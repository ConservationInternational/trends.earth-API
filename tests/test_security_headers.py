"""Tests for security headers middleware."""

import os
from unittest.mock import patch


class TestSecurityHeaders:
    """Test that security headers are properly set on all responses."""

    def test_security_headers_in_development(self, client):
        """Test security headers in development environment."""
        with patch.dict(os.environ, {"ENVIRONMENT": "dev"}):
            response = client.get("/api-health")

            # Check Content Security Policy
            assert "Content-Security-Policy" in response.headers
            csp = response.headers["Content-Security-Policy"]
            assert "default-src 'self'" in csp
            assert "script-src 'self'" in csp

            # Check X-Content-Type-Options
            assert response.headers["X-Content-Type-Options"] == "nosniff"

            # Check X-Frame-Options
            assert response.headers["X-Frame-Options"] == "DENY"

            # Check X-XSS-Protection
            assert response.headers["X-XSS-Protection"] == "1; mode=block"

            # Check Referrer-Policy
            assert "Referrer-Policy" in response.headers

            # Check Permissions-Policy
            assert "Permissions-Policy" in response.headers

            # HSTS should NOT be present in development
            assert "Strict-Transport-Security" not in response.headers

    def test_hsts_header_in_production(self, client):
        """Test that HSTS header is added in production environment."""
        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}):
            response = client.get("/api-health")

            # HSTS should be present in production
            assert "Strict-Transport-Security" in response.headers
            hsts = response.headers["Strict-Transport-Security"]
            assert "max-age=31536000" in hsts
            assert "includeSubDomains" in hsts
            assert "preload" in hsts

    def test_security_headers_on_api_endpoints(self, client):
        """Test security headers on API endpoints."""
        response = client.get("/api/v1/script")

        # All security headers should be present
        assert "Content-Security-Policy" in response.headers
        assert "X-Content-Type-Options" in response.headers
        assert "X-Frame-Options" in response.headers
        assert "X-XSS-Protection" in response.headers
        assert "Referrer-Policy" in response.headers
        assert "Permissions-Policy" in response.headers

    def test_csp_header_details(self, client):
        """Test Content Security Policy header details."""
        response = client.get("/api-health")
        csp = response.headers.get("Content-Security-Policy", "")

        # Verify CSP directives
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "style-src 'self'" in csp
        assert "img-src 'self' data: https:" in csp
        assert "connect-src 'self'" in csp

    def test_permissions_policy_disables_features(self, client):
        """Test that Permissions-Policy disables unnecessary features."""
        response = client.get("/api-health")
        permissions = response.headers.get("Permissions-Policy", "")

        # Verify dangerous features are disabled
        assert "geolocation=()" in permissions
        assert "microphone=()" in permissions
        assert "camera=()" in permissions
        assert "payment=()" in permissions

    def test_security_headers_on_error_responses(self, client):
        """Test security headers are present even on error responses."""
        response = client.get("/api/v1/nonexistent-endpoint")

        # Security headers should be present even on 404
        assert "X-Content-Type-Options" in response.headers
        assert "X-Frame-Options" in response.headers

    def test_x_frame_options_prevents_clickjacking(self, client):
        """Test X-Frame-Options prevents clickjacking attacks."""
        response = client.get("/api-health")

        # Should deny all framing
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_x_content_type_options_prevents_mime_sniffing(self, client):
        """Test X-Content-Type-Options prevents MIME sniffing."""
        response = client.get("/api-health")

        # Should prevent MIME type sniffing
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_referrer_policy_limits_information_leakage(self, client):
        """Test Referrer-Policy limits referrer information leakage."""
        response = client.get("/api-health")

        # Should have strict referrer policy
        assert "Referrer-Policy" in response.headers
        referrer_policy = response.headers["Referrer-Policy"]
        assert "strict-origin" in referrer_policy.lower()


class TestHSTSConfiguration:
    """Test HTTPS Strict Transport Security configuration."""

    def test_hsts_max_age_is_one_year(self, client):
        """Test HSTS max-age is set to 1 year (31536000 seconds)."""
        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}):
            response = client.get("/api-health")

            hsts = response.headers.get("Strict-Transport-Security", "")
            assert "max-age=31536000" in hsts

    def test_hsts_includes_subdomains(self, client):
        """Test HSTS includes subdomains directive."""
        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}):
            response = client.get("/api-health")

            hsts = response.headers.get("Strict-Transport-Security", "")
            assert "includeSubDomains" in hsts

    def test_hsts_includes_preload(self, client):
        """Test HSTS includes preload directive."""
        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}):
            response = client.get("/api-health")

            hsts = response.headers.get("Strict-Transport-Security", "")
            assert "preload" in hsts

    def test_hsts_not_present_in_staging(self, client):
        """Test HSTS is not present in staging environment."""
        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}):
            response = client.get("/api-health")

            # HSTS should only be in production
            assert "Strict-Transport-Security" not in response.headers
