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
    """Rate limiting configuration class"""

    @staticmethod
    def _get_config():
        """Get configuration from Flask app or fallback to global SETTINGS"""
        try:
            # Use Flask app config if available (for testing)
            return current_app.config
        except RuntimeError:
            # Fallback to global SETTINGS if no app context
            return SETTINGS

    @staticmethod
    def is_enabled():
        """Check if rate limiting is enabled"""
        config = RateLimitConfig._get_config()
        return config.get("RATE_LIMITING", {}).get("ENABLED", True)

    @staticmethod
    def get_storage_uri():
        """Get the storage URI for rate limiting"""
        config = RateLimitConfig._get_config()
        rate_limit_config = config.get("RATE_LIMITING", {})
        return (
            rate_limit_config.get("STORAGE_URI")
            or config.get("REDIS_URL")
            or config.get("CELERY_BROKER_URL")
        )

    @staticmethod
    def get_default_limits():
        """Get default rate limits"""
        config = RateLimitConfig._get_config()
        return config.get("RATE_LIMITING", {}).get("DEFAULT_LIMITS", ["1000 per hour"])

    @staticmethod
    def get_auth_limits():
        """Get authentication rate limits"""
        config = RateLimitConfig._get_config()
        return config.get("RATE_LIMITING", {}).get("AUTH_LIMITS", ["5 per minute"])

    @staticmethod
    def get_password_reset_limits():
        """Get password reset rate limits"""
        config = RateLimitConfig._get_config()
        return config.get("RATE_LIMITING", {}).get(
            "PASSWORD_RESET_LIMITS", ["3 per hour"]
        )

    @staticmethod
    def get_api_limits():
        """Get general API rate limits"""
        config = RateLimitConfig._get_config()
        return config.get("RATE_LIMITING", {}).get("API_LIMITS", ["500 per hour"])

    @staticmethod
    def get_user_creation_limits():
        """Get user creation rate limits"""
        config = RateLimitConfig._get_config()
        return config.get("RATE_LIMITING", {}).get(
            "USER_CREATION_LIMITS", ["10 per hour"]
        )

    @staticmethod
    def get_execution_run_limits():
        """Get script execution run rate limits"""
        config = RateLimitConfig._get_config()
        return config.get("RATE_LIMITING", {}).get(
            "EXECUTION_RUN_LIMITS", ["10 per minute", "40 per hour"]
        )


def bypass_rate_limiting():
    """
    Check if rate limiting should be bypassed for this request.
    Useful for testing or admin overrides.
    """
    try:
        config = current_app.config
    except RuntimeError:
        config = SETTINGS

    # Bypass in testing mode
    if config.get("TESTING", False):
        return True

    # Bypass if disabled in config
    if not RateLimitConfig.is_enabled():
        return True

    # Check for admin bypass header (only in development)
    if config.get("ENV") == "development":
        return request.headers.get("X-Bypass-Rate-Limit") == "true"

    return False


def rate_limit_error_handler(error):
    """Custom error handler for rate limit exceeded"""
    # Try to get retry_after from the error object, fallback to None
    retry_after = getattr(error, "retry_after", None)
    return create_rate_limit_response(retry_after=retry_after)
