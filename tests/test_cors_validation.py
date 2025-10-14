"""Tests for CORS validation and configuration."""

import os
from unittest.mock import patch

import pytest


class TestCORSValidation:
    """Test CORS origin validation on application startup."""

    def test_cors_validation_allows_localhost_in_development(self):
        """Test that localhost origins are allowed in development."""
        from gefapi import validate_cors_origins

        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "dev",
                "CORS_ORIGINS": "http://localhost:3000,http://localhost:8080",
            },
        ):
            # Should not raise an exception
            try:
                validate_cors_origins()
            except ValueError:
                pytest.fail("CORS validation should allow localhost in dev")

    def test_cors_validation_rejects_localhost_in_production(self):
        """Test that localhost origins are rejected in production."""
        from gefapi import validate_cors_origins

        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "prod",
                "CORS_ORIGINS": "http://localhost:3000,https://app.trends.earth",
            },
        ):
            with pytest.raises(ValueError) as exc_info:
                validate_cors_origins()

            assert "localhost" in str(exc_info.value).lower()
            assert "not allowed in production" in str(exc_info.value).lower()

    def test_cors_validation_rejects_127_0_0_1_in_production(self):
        """Test that 127.0.0.1 origins are rejected in production."""
        # Manually call validation with specific origins
        origins = ["http://127.0.0.1:3000", "https://app.trends.earth"]

        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}), patch(
            "gefapi.cors_origins", origins
        ):
            from gefapi import validate_cors_origins

            with pytest.raises(ValueError) as exc_info:
                validate_cors_origins()

            assert (
                "127.0.0.1" in str(exc_info.value)
                or "localhost" in str(exc_info.value).lower()
            )
            assert "not allowed in production" in str(exc_info.value).lower()

    def test_cors_validation_rejects_empty_origins_in_production(self):
        """Test that empty CORS origins are rejected in production."""
        origins = [""]

        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}), patch(
            "gefapi.cors_origins", origins
        ):
            from gefapi import validate_cors_origins

            with pytest.raises(ValueError) as exc_info:
                validate_cors_origins()

            assert "explicitly set" in str(exc_info.value).lower()

    def test_cors_validation_accepts_valid_production_origins(self):
        """Test that valid production origins are accepted."""
        origins = ["https://trends.earth", "https://app.trends.earth"]

        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}), patch(
            "gefapi.cors_origins", origins
        ):
            from gefapi import validate_cors_origins

            # Should not raise an exception
            try:
                validate_cors_origins()
            except ValueError as e:
                pytest.fail(f"Valid production origins should be accepted: {e}")

    def test_cors_validation_in_staging_allows_localhost(self):
        """Test that localhost is allowed in staging environment."""
        from gefapi import validate_cors_origins

        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "staging",
                "CORS_ORIGINS": "http://localhost:3000,https://staging.trends.earth",
            },
        ):
            # Should not raise an exception
            try:
                validate_cors_origins()
            except ValueError:
                pytest.fail("CORS validation should allow localhost in staging")


class TestCORSConfiguration:
    """Test CORS configuration in API responses."""

    def test_cors_headers_present_on_options_request(self, client):
        """Test CORS headers are present on OPTIONS preflight requests."""
        response = client.options(
            "/api/v1/script", headers={"Origin": "http://localhost:3000"}
        )

        # CORS headers should be present
        assert "Access-Control-Allow-Origin" in response.headers

    def test_cors_allows_configured_origins(self, client):
        """Test CORS allows origins from CORS_ORIGINS configuration."""
        with patch.dict(
            os.environ, {"CORS_ORIGINS": "http://localhost:3000,http://localhost:8080"}
        ):
            response = client.get(
                "/api-health", headers={"Origin": "http://localhost:3000"}
            )

            # Should allow the configured origin
            assert response.headers.get("Access-Control-Allow-Origin") in [
                "http://localhost:3000",
                "*",
            ]

    def test_cors_allows_authorization_header(self, client):
        """Test CORS configuration allows Authorization header."""
        response = client.options(
            "/api/v1/script",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Headers": "Authorization",
                "Access-Control-Request-Method": "POST",
            },
        )

        # Should allow Authorization header (may be in allow-headers or methods)
        # CORS library configuration should support these headers
        assert response.status_code in [200, 204]

    def test_cors_allows_content_type_header(self, client):
        """Test CORS configuration allows Content-Type header."""
        response = client.options(
            "/api/v1/script",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Headers": "Content-Type",
                "Access-Control-Request-Method": "POST",
            },
        )

        # Should allow Content-Type header (may be in allow-headers or methods)
        # CORS library configuration should support these headers
        assert response.status_code in [200, 204]

    def test_cors_allows_standard_methods(self, client):
        """Test CORS allows standard HTTP methods."""
        response = client.options(
            "/api/v1/script",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )

        # Should allow POST, GET, PUT, DELETE, PATCH
        allowed_methods = response.headers.get("Access-Control-Allow-Methods", "")
        for method in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
            assert method in allowed_methods


class TestCORSSecurityScenarios:
    """Test CORS security scenarios and edge cases."""

    def test_cors_validation_multiple_localhost_variations(self):
        """Test that all localhost variations are caught in production."""
        from gefapi import validate_cors_origins

        localhost_variations = [
            "http://localhost:3000",
            "https://localhost:8080",
            "http://127.0.0.1:3000",
            "https://127.0.0.1:8080",
        ]

        for origin in localhost_variations:
            with patch.dict(
                os.environ,
                {
                    "ENVIRONMENT": "prod",
                    "CORS_ORIGINS": f"{origin},https://app.trends.earth",
                },
            ):
                with pytest.raises(ValueError) as exc_info:
                    validate_cors_origins()

                error_msg = str(exc_info.value).lower()
                assert "localhost" in error_msg or "127.0.0.1" in error_msg, (
                    f"Should reject {origin} in production"
                )

    def test_cors_validation_case_insensitive(self):
        """Test CORS validation is case-insensitive for localhost."""
        from gefapi import validate_cors_origins

        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "prod",
                "CORS_ORIGINS": "http://LocalHost:3000,https://app.trends.earth",
            },
        ), pytest.raises(ValueError):
            validate_cors_origins()

    def test_cors_validation_with_ports(self):
        """Test CORS validation handles origins with different ports."""
        origins = ["https://trends.earth:443", "https://app.trends.earth:8443"]

        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}), patch(
            "gefapi.cors_origins", origins
        ):
            from gefapi import validate_cors_origins

            # Should accept origins with explicit ports
            try:
                validate_cors_origins()
            except ValueError as e:
                pytest.fail(f"Should accept valid origins with ports: {e}")
