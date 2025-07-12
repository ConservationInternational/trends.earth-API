"""Rate limiting utilities and decorators for the GEF API"""

import logging

from flask import current_app, jsonify, request
from flask_jwt_extended import get_current_user, verify_jwt_in_request
from flask_limiter.util import get_remote_address

from gefapi.config import SETTINGS

logger = logging.getLogger(__name__)


def is_rate_limiting_disabled():
    """Helper function for exempt_when parameter to check if rate limiting is
    disabled"""
    # Check testing mode first - this bypasses rate limiting in tests
    if bypass_rate_limiting():
        return True

    enabled = RateLimitConfig.is_enabled()
    # Also check if Flask-Limiter is globally disabled
    from gefapi import limiter

    if hasattr(limiter, "enabled"):
        enabled = enabled and limiter.enabled
    return not enabled


def get_user_id_or_ip():
    """
    Get user ID for authenticated requests, IP address for anonymous requests.
    This provides better rate limiting granularity.
    Returns None if user should be exempt from rate limiting.
    """
    try:
        verify_jwt_in_request(optional=True)
        current_user = get_current_user()
        if current_user:
            # Exempt admin and superadmin users from rate limiting
            if current_user.role in ["ADMIN", "SUPERADMIN"]:
                return None  # None indicates no rate limiting
            return f"user:{current_user.id}"
    except Exception as e:
        logger.debug(f"Failed to get current user for rate limiting: {e}")
    return f"ip:{get_remote_address()}"


def get_rate_limit_key_for_auth():
    """
    Special key function for authentication endpoints.
    Uses email + IP to prevent account enumeration while still allowing rate limiting.
    Returns None if user should be exempt from rate limiting.
    """
    # Check if this is an authenticated admin/superadmin user trying to get a new token
    try:
        verify_jwt_in_request(optional=True)
        current_user = get_current_user()
        if current_user and current_user.role in ["ADMIN", "SUPERADMIN"]:
            return None  # Exempt from rate limiting
    except Exception as e:
        logger.debug(f"Failed to get current user for auth rate limiting: {e}")

    email = request.json.get("email", "") if request.json else ""
    ip = get_remote_address()
    if email:
        # Hash the email to prevent log leakage while maintaining rate limiting
        import hashlib

        email_hash = hashlib.sha256(email.encode()).hexdigest()[:16]
        return f"auth:{email_hash}:{ip}"
    return f"auth:anon:{ip}"


def get_admin_aware_key():
    """
    Key function that exempts admin and superadmin users from rate limiting.
    Used for general API endpoints.
    """
    try:
        verify_jwt_in_request(optional=True)
        current_user = get_current_user()
        if current_user:
            # Exempt admin and superadmin users from rate limiting
            if current_user.role in ["ADMIN", "SUPERADMIN"]:
                return None  # None indicates no rate limiting
            return f"user:{current_user.id}"
    except Exception as e:
        logger.debug(f"Failed to get current user for admin-aware rate limiting: {e}")
    return f"ip:{get_remote_address()}"


def create_rate_limit_response(retry_after=None):
    """Create a standardized rate limit exceeded response"""
    response_data = {
        "status": 429,
        "detail": "Rate limit exceeded. Please try again later.",
        "error_code": "RATE_LIMIT_EXCEEDED",
    }

    if retry_after:
        response_data["retry_after"] = retry_after

    response = jsonify(response_data)
    response.status_code = 429

    if retry_after:
        response.headers["Retry-After"] = str(retry_after)

    return response


class RateLimitConfig:
    """Helper class to centralize rate limit configuration."""

    @classmethod
    def _get_config(cls):
        """Get rate limiting config from Flask app config or fallback to SETTINGS"""
        try:
            # Try to get from Flask app config first (for testing)
            return current_app.config.get("RATE_LIMITING", {})
        except RuntimeError:
            # Fallback to SETTINGS if no app context
            return SETTINGS.get("RATE_LIMITING", {})

    @classmethod
    def is_enabled(cls):
        """Check if rate limiting is globally enabled."""
        return cls._get_config().get("ENABLED", True)

    @classmethod
    def get_storage_uri(cls):
        """Get the storage URI for the rate limiter."""
        config = cls._get_config()
        # Fallback to other redis URLs for convenience
        return (
            config.get("STORAGE_URI")
            or SETTINGS.get("REDIS_URL")
            or SETTINGS.get("CELERY_BROKER_URL")
        )

    @classmethod
    def get_default_limits(cls):
        """Get the global default rate limits."""
        return cls._get_config().get("DEFAULT_LIMITS", ["1000 per hour"])

    @classmethod
    def get_auth_limits(cls):
        """Get rate limits for authentication endpoints."""
        return cls._get_config().get("AUTH_LIMITS", ["5 per minute"])

    @classmethod
    def get_password_reset_limits(cls):
        """Get rate limits for password reset endpoints."""
        return cls._get_config().get("PASSWORD_RESET_LIMITS", ["3 per hour"])

    @classmethod
    def get_api_limits(cls):
        """Get general API rate limits."""
        return cls._get_config().get("API_LIMITS", ["500 per hour"])

    @classmethod
    def get_user_creation_limits(cls):
        """Get user creation rate limits."""
        return cls._get_config().get("USER_CREATION_LIMITS", ["10 per hour"])

    @classmethod
    def get_execution_run_limits(cls):
        """Get script execution endpoints."""
        return cls._get_config().get(
            "EXECUTION_RUN_LIMITS", ["10 per minute", "40 per hour"]
        )


def bypass_rate_limiting():
    """
    Check if rate limiting should be bypassed for this request.
    Useful for testing or admin overrides.
    """
    try:
        config = current_app.config

        # Bypass in testing mode
        if config.get("TESTING", False):
            # Check if rate limiting is explicitly enabled in test config
            test_rate_config = config.get("RATE_LIMITING", {})
            if not test_rate_config.get("ENABLED", True):
                return True
    except RuntimeError:
        config = SETTINGS

    # Bypass if disabled in config
    if not RateLimitConfig.is_enabled():
        return True

    # Check for admin bypass header (only in development)
    try:
        if current_app.config.get("ENV") == "development":
            return request.headers.get("X-Bypass-Rate-Limit") == "true"
    except (RuntimeError, AttributeError):
        pass

    return False


def rate_limit_error_handler(error):
    """Custom error handler for rate limit exceeded"""
    # Try to get retry_after from the error object, fallback to None
    retry_after = getattr(error, "retry_after", None)
    return create_rate_limit_response(retry_after=retry_after)


def reconfigure_limiter_for_testing():
    """
    Reconfigure the Flask-Limiter instance for testing.
    This should be called after test configuration is applied.
    """
    try:
        from gefapi import limiter

        # Get the current app config
        test_config = current_app.config.get("RATE_LIMITING", {})

        # Update default limits
        new_default_limits = test_config.get("DEFAULT_LIMITS")
        if new_default_limits:
            limiter._default_limits = new_default_limits

        # Ensure limiter is enabled unless explicitly disabled
        limiter.enabled = test_config.get("ENABLED", True)

        # For memory storage in tests, clear any existing data
        storage_uri = test_config.get("STORAGE_URI", "")
        if "memory://" in storage_uri and hasattr(limiter, "_storage"):
            try:
                if hasattr(limiter._storage, "storage"):
                    limiter._storage.storage.clear()
                elif hasattr(limiter._storage, "reset"):
                    limiter._storage.reset()
            except Exception as e:
                # Best effort cleanup - log the exception for debugging
                logger.debug(f"Rate limiter cleanup failed: {e}")

        return True
    except Exception as e:
        logger.warning(f"Failed to reconfigure limiter for testing: {e}")
        return False
