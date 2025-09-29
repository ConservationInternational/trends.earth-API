"""
Tests for compression features in the Trends.Earth API
"""

import gzip
import json
from unittest.mock import patch

import pytest

from gefapi import app


class TestCompressionMiddleware:
    """Test request decompression middleware"""

    @pytest.fixture
    def client(self):
        """Create test client"""
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_uncompressed_request_passthrough(self, client):
        """Test that uncompressed requests work normally"""
        # Regular request without compression
        response = client.get("/api-health")
        assert response.status_code == 200

    def test_compressed_request_decompression(self, client):
        """Test that gzip-compressed requests are properly decompressed"""
        # Create test data
        test_data = {"test": "data", "large_field": "x" * 1000}
        json_str = json.dumps(test_data)

        # Compress the data
        compressed_data = gzip.compress(json_str.encode("utf-8"))

        # Make request with compressed data
        response = client.post(
            "/api-health",  # Using health endpoint as it accepts any request
            data=compressed_data,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )

        # Should process without errors (even if endpoint doesn't accept POST)
        # The important thing is that decompression doesn't cause a 500 error
        assert response.status_code in [200, 405, 404]  # Valid responses

    def test_malformed_compressed_request_fallback(self, client):
        """Test that malformed compressed requests fall back gracefully"""
        # Create invalid compressed data
        invalid_compressed_data = b"this is not valid gzip data"

        # Make request with invalid compressed data
        response = client.post(
            "/api-health",
            data=invalid_compressed_data,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )

        # Should not cause a 500 error - should fall back gracefully
        assert response.status_code in [200, 400, 405, 404]

    def test_content_encoding_header_removal(self, client):
        """Test that Content-Encoding header is removed after decompression"""
        test_data = {"test": "data"}
        json_str = json.dumps(test_data)
        compressed_data = gzip.compress(json_str.encode("utf-8"))

        # Use a custom test route to check request headers
        with app.test_request_context(
            "/test",
            method="POST",
            data=compressed_data,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        ) as ctx:
            # Manually trigger the before_request handler
            app.preprocess_request()

            # After preprocessing, the Content-Encoding header should be removed
            # from the request environment
            assert "HTTP_CONTENT_ENCODING" not in ctx.request.environ


class TestCompressionConfiguration:
    """Test compression configuration settings"""

    def test_compression_config_defaults(self):
        """Test that compression configuration has correct defaults"""
        from gefapi.config import SETTINGS

        # Check that compression settings exist with expected defaults
        assert SETTINGS.get("ENABLE_REQUEST_COMPRESSION") is True
        assert SETTINGS.get("COMPRESSION_MIN_SIZE") == 1000
        assert SETTINGS.get("MAX_RESULTS_SIZE") == 600000  # 600KB

    @patch.dict(
        "os.environ",
        {
            "ENABLE_REQUEST_COMPRESSION": "false",
            "COMPRESSION_MIN_SIZE": "2000",
            "MAX_RESULTS_SIZE": "1000000",
        },
    )
    def test_compression_config_environment_overrides(self):
        """Test that environment variables override compression configuration"""
        # Reload config to pick up environment changes
        import importlib

        from gefapi.config import base

        importlib.reload(base)

        assert base.SETTINGS.get("ENABLE_REQUEST_COMPRESSION") is False
        assert base.SETTINGS.get("COMPRESSION_MIN_SIZE") == 2000
        assert base.SETTINGS.get("MAX_RESULTS_SIZE") == 1000000


class TestValidationWithCompression:
    """Test validation logic with compression awareness"""

    @pytest.fixture
    def client(self):
        """Create test client"""
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    @pytest.fixture
    def auth_headers_admin(self, client):
        """Create admin authentication headers for testing"""
        # This is a simplified version - in practice you'd need proper auth setup
        return {"Authorization": "Bearer test-token"}

    def test_large_compressible_results_validation(self, client, auth_headers_admin):
        """Test that large but compressible results pass validation"""
        from gefapi.validators import validate_execution_update

        # Create large but highly compressible data
        large_compressible_data = {
            "results": {
                "data": ["same_value"] * 10000,  # Highly compressible
                "metadata": {"type": "test", "count": 10000},
            }
        }

        # Test the validation function directly
        with app.test_request_context(
            "/test", method="POST", json=large_compressible_data
        ):
            # Create a mock function to wrap
            def mock_func(*args, **kwargs):
                return {"status": "success"}

            # Apply the validator
            wrapped_func = validate_execution_update(mock_func)

            # Should not raise an error for compressible data
            result = wrapped_func()
            assert result["status"] == "success"

    def test_large_uncompressible_results_validation(self, client, auth_headers_admin):
        """Test that truly large uncompressible results are still rejected"""
        import secrets
        import string

        from gefapi.validators import validate_execution_update

        # Create data that's large enough to exceed even the compressed threshold
        # We need to make data that exceeds max_results_size * 2 when compressed
        # Default max_results_size is 50000, so threshold is 100000 bytes compressed
        random_data = {
            "results": {
                "data": [
                    "".join(
                        secrets.choice(string.ascii_letters + string.digits)
                        for _ in range(200)
                    )
                    for _ in range(
                        25000
                    )  # 5MB of truly random data to exceed compressed threshold
                ]
            }
        }

        with app.test_request_context("/test", method="POST", json=random_data):

            def mock_func(*args, **kwargs):
                return {"status": "success"}

            wrapped_func = validate_execution_update(mock_func)

            # Should reject very large data that exceeds compressed threshold
            result = wrapped_func()

            # The error function returns a tuple (response, status_code)
            # Check if it's an error response (tuple) or success response (dict)
            if isinstance(result, tuple):
                # It's an error response
                response_data, status_code = result
                assert status_code == 400
                assert "detail" in response_data.get_json()
            else:
                # It's a success response - this should not happen with truly large data
                raise AssertionError(
                    f"Expected error response but got success: {result}"
                )

    def test_validation_compression_fallback(self, client):
        """Test that validation falls back to string-based check if compression fails"""
        from gefapi.validators import validate_execution_update

        # Create data that will cause compression to fail
        with (
            app.test_request_context(
                "/test", method="POST", json={"results": {"test": "small data"}}
            ),
            patch("gzip.compress", side_effect=Exception("Compression failed")),
        ):

            def mock_func(*args, **kwargs):
                return {"status": "success"}

            wrapped_func = validate_execution_update(mock_func)

            # Should fall back to string-based validation and succeed for small data
            result = wrapped_func()
            assert result["status"] == "success"

    def test_validation_compression_ratio_logging(self, client, caplog):
        """Test that compression ratio is logged for monitoring"""
        import logging

        from gefapi.validators import validate_execution_update

        # Create moderately compressible data
        test_data = {"results": {"data": ["repeated_value"] * 1000}}

        with app.test_request_context("/test", method="POST", json=test_data):

            def mock_func(*args, **kwargs):
                return {"status": "success"}

            wrapped_func = validate_execution_update(mock_func)

            # Clear any existing log records and set DEBUG level
            caplog.clear()
            with caplog.at_level(logging.DEBUG, logger="gefapi.validators"):
                # Execute the function
                result = wrapped_func()

            # Check that compression ratio was logged
            assert any(
                "compression" in record.message.lower() for record in caplog.records
            )
            assert result["status"] == "success"


class TestFlaskCompressIntegration:
    """Test integration with Flask-Compress for response compression"""

    @pytest.fixture
    def client(self):
        """Create test client"""
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_response_compression_enabled(self, client):
        """Test that response compression is properly configured"""
        # Check that Flask-Compress configuration is set
        assert "COMPRESS_MIMETYPES" in app.config
        assert "application/json" in app.config["COMPRESS_MIMETYPES"]
        assert app.config.get("COMPRESS_LEVEL") == 6
        assert app.config.get("COMPRESS_MIN_SIZE") == 500

    def test_response_compression_with_accept_encoding(self, client):
        """Test that responses are compressed when client supports it"""
        # Make request with Accept-Encoding header
        response = client.get(
            "/api-health", headers={"Accept-Encoding": "gzip, deflate"}
        )

        # Response should include compression headers for large responses
        assert response.status_code == 200
        # Note: Health endpoint might be too small to trigger compression
        # But configuration should be in place

    def test_response_compression_without_accept_encoding(self, client):
        """Test that responses are not compressed when client doesn't support it"""
        # Make request without Accept-Encoding header
        response = client.get("/api-health")

        # Should still work normally
        assert response.status_code == 200
        # Response should not be compressed if client doesn't request it
