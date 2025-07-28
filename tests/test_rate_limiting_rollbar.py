"""Tests for Rollbar notifications on rate limiting"""

from unittest.mock import MagicMock, patch

from gefapi.utils.rate_limiting import (
    create_rate_limit_response,
    rate_limit_error_handler,
)


class TestRateLimitingRollbarNotifications:
    """Test Rollbar notifications when rate limits are applied"""

    @patch("gefapi.utils.rate_limiting.rollbar")
    @patch("gefapi.utils.rate_limiting.get_current_user")
    @patch("gefapi.utils.rate_limiting.verify_jwt_in_request")
    @patch("gefapi.utils.rate_limiting.get_remote_address")
    def test_rollbar_notification_with_authenticated_user(
        self,
        mock_get_remote_address,
        mock_verify_jwt,
        mock_get_current_user,
        mock_rollbar,
        client,
    ):
        """Test that Rollbar notification is sent when rate limit is applied to authenticated user"""

        # Mock user data
        mock_user = MagicMock()
        mock_user.id = 123
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user.role = "USER"

        mock_get_current_user.return_value = mock_user
        mock_get_remote_address.return_value = "192.168.1.100"
        mock_verify_jwt.return_value = None  # No exception

        with client.application.test_request_context(
            "/api/v1/user/me", method="GET", headers={"User-Agent": "Test Agent"}
        ):
            # Call the rate limit response function
            response = create_rate_limit_response(retry_after=60)

            # Verify response
            assert response.status_code == 429
            response_data = response.get_json()
            assert response_data["status"] == 429
            assert response_data["error_code"] == "RATE_LIMIT_EXCEEDED"
            assert response.headers.get("Retry-After") == "60"

            # Verify Rollbar was called
            mock_rollbar.report_message.assert_called_once()
            call_args = mock_rollbar.report_message.call_args

            # Check the message
            assert (
                "Rate limit applied to user test@example.com (ID: 123)"
                in call_args[1]["message"]
            )
            assert "endpoint" in call_args[1]["message"]

            # Check the level
            assert call_args[1]["level"] == "warning"

            # Check the extra data
            extra_data = call_args[1]["extra_data"]
            assert extra_data["user_id"] == 123
            assert extra_data["ip_address"] == "192.168.1.100"
            assert extra_data["method"] == "GET"
            assert extra_data["user_agent"] == "Test Agent"
            assert extra_data["retry_after"] == 60
            assert extra_data["user_info"]["email"] == "test@example.com"
            assert extra_data["user_info"]["role"] == "USER"

    @patch("gefapi.utils.rate_limiting.rollbar")
    @patch("gefapi.utils.rate_limiting.get_current_user")
    @patch("gefapi.utils.rate_limiting.verify_jwt_in_request")
    @patch("gefapi.utils.rate_limiting.get_remote_address")
    def test_rollbar_notification_with_unauthenticated_user(
        self,
        mock_get_remote_address,
        mock_verify_jwt,
        mock_get_current_user,
        mock_rollbar,
        client,
    ):
        """Test that Rollbar notification is sent when rate limit is applied to unauthenticated user"""

        # Mock unauthenticated user
        mock_get_current_user.return_value = None
        mock_get_remote_address.return_value = "10.0.0.50"
        mock_verify_jwt.side_effect = Exception("No JWT token")  # Simulate no token

        with client.application.test_request_context(
            "/auth", method="POST", headers={"User-Agent": "Auth Client"}
        ):
            # Call the rate limit response function
            response = create_rate_limit_response(retry_after=300)

            # Verify response
            assert response.status_code == 429
            response_data = response.get_json()
            assert response_data["status"] == 429
            assert response_data["error_code"] == "RATE_LIMIT_EXCEEDED"
            assert response.headers.get("Retry-After") == "300"

            # Verify Rollbar was called
            mock_rollbar.report_message.assert_called_once()
            call_args = mock_rollbar.report_message.call_args

            # Check the message for unauthenticated user
            assert "Rate limit applied to IP 10.0.0.50" in call_args[1]["message"]
            assert "endpoint" in call_args[1]["message"]

            # Check the level
            assert call_args[1]["level"] == "warning"

            # Check the extra data
            extra_data = call_args[1]["extra_data"]
            assert extra_data["user_id"] is None
            assert extra_data["ip_address"] == "10.0.0.50"
            assert extra_data["method"] == "POST"
            assert extra_data["user_agent"] == "Auth Client"
            assert extra_data["retry_after"] == 300
            assert extra_data["user_info"] is None

    @patch("gefapi.utils.rate_limiting.rollbar")
    @patch("gefapi.utils.rate_limiting.get_remote_address")
    def test_rollbar_notification_handles_rollbar_errors(
        self, mock_get_remote_address, mock_rollbar, client
    ):
        """Test that Rollbar errors don't prevent rate limit response"""

        mock_get_remote_address.return_value = "172.16.0.1"
        # Make Rollbar throw an exception
        mock_rollbar.report_message.side_effect = Exception("Rollbar service error")

        with client.application.test_request_context("/api/v1/scripts", method="GET"):
            # Call the rate limit response function
            response = create_rate_limit_response()

            # Verify response is still created despite Rollbar error
            assert response.status_code == 429
            response_data = response.get_json()
            assert response_data["status"] == 429
            assert response_data["error_code"] == "RATE_LIMIT_EXCEEDED"

            # Verify Rollbar was attempted to be called
            mock_rollbar.report_message.assert_called_once()

    @patch("gefapi.utils.rate_limiting.create_rate_limit_response")
    def test_rate_limit_error_handler_calls_create_response(
        self, mock_create_response, client
    ):
        """Test that rate_limit_error_handler properly calls create_rate_limit_response"""

        # Mock the error object
        mock_error = MagicMock()
        mock_error.retry_after = 120

        # Mock the response
        mock_response = MagicMock()
        mock_create_response.return_value = mock_response

        with client.application.test_request_context():
            # Call the error handler
            result = rate_limit_error_handler(mock_error)

            # Verify it called create_rate_limit_response with correct retry_after
            mock_create_response.assert_called_once_with(retry_after=120)
            assert result == mock_response

    @patch("gefapi.utils.rate_limiting.create_rate_limit_response")
    def test_rate_limit_error_handler_handles_missing_retry_after(
        self, mock_create_response, client
    ):
        """Test that rate_limit_error_handler handles errors without retry_after"""

        # Mock the error object without retry_after
        mock_error = MagicMock()
        del mock_error.retry_after  # Simulate missing attribute

        # Mock the response
        mock_response = MagicMock()
        mock_create_response.return_value = mock_response

        with client.application.test_request_context():
            # Call the error handler
            result = rate_limit_error_handler(mock_error)

            # Verify it called create_rate_limit_response with None retry_after
            mock_create_response.assert_called_once_with(retry_after=None)
            assert result == mock_response

    @patch("gefapi.utils.rate_limiting.rollbar")
    @patch("gefapi.utils.rate_limiting.get_current_user")
    @patch("gefapi.utils.rate_limiting.verify_jwt_in_request")
    @patch("gefapi.utils.rate_limiting.get_remote_address")
    def test_rollbar_notification_different_endpoints(
        self,
        mock_get_remote_address,
        mock_verify_jwt,
        mock_get_current_user,
        mock_rollbar,
        client,
    ):
        """Test that Rollbar notifications include correct endpoint information"""

        mock_get_current_user.return_value = None
        mock_get_remote_address.return_value = "203.0.113.42"
        mock_verify_jwt.side_effect = Exception("No token")

        test_endpoints = [
            ("/auth", "POST"),
            ("/api/v1/user", "GET"),
            ("/api/v1/script/test-script/run", "POST"),
            ("/api/v1/user/recovery", "POST"),
        ]

        for endpoint, method in test_endpoints:
            with client.application.test_request_context(endpoint, method=method):
                create_rate_limit_response()

                # Get the last call to rollbar
                call_args = mock_rollbar.report_message.call_args
                extra_data = call_args[1]["extra_data"]

                # Verify endpoint information is included
                assert extra_data["endpoint"] == endpoint
                assert extra_data["method"] == method
                assert endpoint in call_args[1]["message"]

            # Reset mock for next iteration
            mock_rollbar.reset_mock()
