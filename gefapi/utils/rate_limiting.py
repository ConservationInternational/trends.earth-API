"""Rate limiting utilities and decorators for the GEF API"""

import logging

from flask import current_app, jsonify, request
from flask_jwt_extended import get_current_user, verify_jwt_in_request
from flask_limiter.util import get_remote_address
import rollbar

from gefapi.config import SETTINGS
from gefapi.utils.permissions import is_admin_or_higher
from gefapi.utils.security_events import log_rate_limit_exceeded

logger = logging.getLogger(__name__)


def is_internal_network_request():
    """
    Check if the request is coming from an internal Docker network.
    Internal networks (like Docker execution network) should bypass rate limiting
    to allow execution containers to communicate with the API.
    """
    try:
        remote_ip = get_remote_address()

        # Get network ranges from configuration with optional overrides
        import os

        internal_networks = list(SETTINGS.get("INTERNAL_NETWORKS", []))

        # Add execution network subnets from environment
        execution_subnet = os.getenv("EXECUTION_SUBNET")
        if execution_subnet:
            internal_networks.append(execution_subnet)

        # Add backend network subnet from environment (fallback compatibility)
        docker_subnet = os.getenv("DOCKER_SUBNET")
        if docker_subnet:
            internal_networks.append(docker_subnet)

        # Additional internal networks can be specified via comma-separated env var
        additional_networks = os.getenv("INTERNAL_NETWORKS")
        if additional_networks:
            internal_networks.extend(
                [net.strip() for net in additional_networks.split(",") if net.strip()]
            )

        import ipaddress

        remote_addr = ipaddress.ip_address(remote_ip)

        for network in internal_networks:
            try:
                if remote_addr in ipaddress.ip_network(network):
                    logger.debug(
                        f"Request from internal execution network: {remote_ip} "
                        f"(network: {network})"
                    )
                    return True
            except ValueError as e:
                logger.warning(f"Invalid network range '{network}': {e}")
                continue

        return False
    except Exception as e:
        logger.debug(f"Failed to check internal network: {e}")
        return False


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
    # Check if this is from an internal network (execution containers)
    if is_internal_network_request():
        return None  # Exempt from rate limiting

    try:
        verify_jwt_in_request(optional=True)
        current_user = get_current_user()
        if current_user:
            # Exempt admin and superadmin users from rate limiting
            if is_admin_or_higher(current_user):
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
    # Check if this is from an internal network (execution containers)
    if is_internal_network_request():
        return None  # Exempt from rate limiting

    # Check if this is an authenticated admin/superadmin user
    # trying to get a new token
    try:
        verify_jwt_in_request(optional=True)
        current_user = get_current_user()
        if current_user and is_admin_or_higher(current_user):
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
    Key function that exempts admin and superadmin users
    from rate limiting, as well as internal network requests.
    Used for general API endpoints.
    """
    # Check if this is from an internal network (execution containers)
    if is_internal_network_request():
        return None  # Exempt from rate limiting

    try:
        verify_jwt_in_request(optional=True)
        current_user = get_current_user()
        if current_user:
            # Exempt admin and superadmin users from rate limiting
            if is_admin_or_higher(current_user):
                return None  # None indicates no rate limiting
            return f"user:{current_user.id}"
    except Exception as e:
        logger.debug(f"Failed to get current user for admin-aware rate limiting: {e}")
    return f"ip:{get_remote_address()}"


def create_rate_limit_response(retry_after=None):
    """
    Create a standardized rate limit exceeded response and send security event
    notification
    """
    # Gather information about the rate limited request
    user_info = None
    user_id = None
    ip_address = get_remote_address()
    endpoint = request.path or request.endpoint

    # Try to get current user information
    try:
        verify_jwt_in_request(optional=True)
        current_user = get_current_user()
        if current_user:
            user_info = {
                "id": current_user.id,
                "email": current_user.email,
                "name": current_user.name,
                "role": current_user.role,
            }
            user_id = current_user.id
    except Exception as e:
        logger.debug(
            f"Could not get current user info for rate limit notification: {e}"
        )

    # Log security event
    log_rate_limit_exceeded(limit_type=endpoint or "unknown_endpoint", user_id=user_id)

    # Send Rollbar notification about the rate limit
    try:
        rollbar_data = {
            "user_id": user_id,
            "ip_address": ip_address,
            "endpoint": endpoint,
            "user_agent": request.headers.get("User-Agent"),
            "method": request.method,
            "url": request.url,
            "retry_after": retry_after,
            "user_info": user_info,
        }

        # Create a descriptive message for Rollbar
        if user_info:
            message = (
                f"Rate limit applied to user {user_info['email']} "
                f"(ID: {user_id}) on endpoint {endpoint}"
            )
        else:
            message = f"Rate limit applied to IP {ip_address} on endpoint {endpoint}"

        # Send notification to Rollbar
        rollbar.report_message(
            message=message, level="warning", extra_data=rollbar_data
        )

        logger.warning(f"Rate limit applied: {message} - Data: {rollbar_data}")

    except Exception as e:
        # Don't let Rollbar errors prevent the rate limit response
        logger.error(f"Failed to send rate limit notification to Rollbar: {e}")

    # Create the response
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
    Useful for testing, admin overrides, or internal network requests.
    """
    try:
        config = current_app.config

        # Bypass for internal network requests (Docker backend network)
        if is_internal_network_request():
            logger.debug("Bypassing rate limiting for internal network request")
            return True

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

    # Log the error for debugging
    logger.info(f"Rate limit exceeded: {error}")

    return create_rate_limit_response(retry_after=retry_after)


def get_current_rate_limits():
    """
    Query the current rate limiting status from Flask-Limiter storage.
    Returns information about which users/IPs are currently rate limited.
    """
    try:
        from gefapi import limiter
        from gefapi.services import UserService

        if not limiter.enabled or not RateLimitConfig.is_enabled():
            return {
                "enabled": False,
                "message": "Rate limiting is currently disabled",
                "active_limits": [],
            }

        storage = limiter._storage
        active_limits = []

        # Try to get all active rate limit keys from storage
        # The method to get keys varies by storage backend
        try:
            # For Redis storage
            if hasattr(storage, "storage") and hasattr(storage.storage, "keys"):
                # Redis storage - get all keys matching rate limit patterns
                pattern = "LIMITER/*"
                keys = storage.storage.keys(pattern)
                if isinstance(keys, list | tuple):
                    rate_limit_keys = [
                        key.decode() if isinstance(key, bytes) else key for key in keys
                    ]
                else:
                    rate_limit_keys = []
            elif hasattr(storage, "storage") and hasattr(storage.storage, "scan_iter"):
                # Redis storage with scan_iter method
                rate_limit_keys = [
                    key.decode() if isinstance(key, bytes) else key
                    for key in storage.storage.scan_iter(match="LIMITER/*")
                ]
            elif hasattr(storage, "storage") and isinstance(storage.storage, dict):
                # Memory storage - direct dictionary access
                rate_limit_keys = list(storage.storage.keys())
            else:
                logger.warning("Unable to determine storage type for rate limit query")
                rate_limit_keys = []

        except Exception as e:
            logger.warning(f"Failed to get rate limit keys from storage: {e}")
            rate_limit_keys = []

        # Process each rate limit key to extract user/IP information
        for key in rate_limit_keys:
            try:
                # Rate limit keys typically follow patterns like:
                # LIMITER/user:12345/60 (per minute limits)
                # LIMITER/ip:192.168.1.1/3600 (per hour limits)
                # LIMITER/auth:hash:ip/60 (auth endpoint limits)

                if not key.startswith("LIMITER/"):
                    continue

                # Extract the rate limit key and time window
                parts = key.split("/")
                if len(parts) < 3:
                    continue

                rate_key = parts[1]  # e.g., "user:12345", "ip:192.168.1.1"
                time_window = parts[2]  # e.g., "60", "3600"

                # Get the current value/count from storage
                try:
                    if hasattr(storage, "get"):
                        current_count = storage.get(key) or 0
                    else:
                        current_count = "unknown"
                except Exception:
                    current_count = "unknown"

                # Parse the rate key to determine if it's a user or IP
                limit_info = {
                    "key": rate_key,
                    "time_window_seconds": time_window,
                    "current_count": current_count,
                    "type": "unknown",
                    "identifier": None,
                    "user_info": None,
                }

                if rate_key.startswith("user:"):
                    user_id = rate_key.split("user:")[1]
                    limit_info["type"] = "user"
                    limit_info["identifier"] = user_id

                    # Try to get user information
                    try:
                        user = UserService.get_user(user_id)
                        if user:
                            limit_info["user_info"] = {
                                "id": user.id,
                                "email": user.email,
                                "name": user.name,
                                "role": user.role,
                            }
                    except Exception as e:
                        logger.debug(f"Failed to get user info for {user_id}: {e}")

                elif rate_key.startswith("ip:"):
                    ip_address = rate_key.split("ip:")[1]
                    limit_info["type"] = "ip"
                    limit_info["identifier"] = ip_address

                elif rate_key.startswith("auth:"):
                    # Auth keys are hashed, so we can't identify specific users
                    limit_info["type"] = "auth"
                    limit_info["identifier"] = rate_key

                # Only include limits that have a current count > 0 (actively limiting)
                if isinstance(current_count, int | float) and current_count > 0:
                    active_limits.append(limit_info)

            except Exception as e:
                logger.debug(f"Failed to process rate limit key {key}: {e}")
                continue

        return {
            "enabled": True,
            "storage_type": type(storage).__name__,
            "total_active_limits": len(active_limits),
            "active_limits": active_limits,
        }

    except Exception as e:
        logger.error(f"Failed to query rate limits: {e}")
        return {
            "enabled": False,
            "error": "Failed to query rate limiting status",
            "message": str(e),
            "active_limits": [],
        }


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
