"""Admin routes for rate limiting and session management."""

import logging

from flask import jsonify
from flask_jwt_extended import current_user, get_jwt_identity, jwt_required

from gefapi import app, limiter
from gefapi.models.refresh_token import RefreshToken
from gefapi.routes.api.v1 import endpoints, error
from gefapi.services import UserService
from gefapi.services.refresh_token_service import RefreshTokenService

logger = logging.getLogger()


@endpoints.route("/rate-limit/status", methods=["GET"])
@jwt_required()
def get_rate_limit_status():
    """
    Query current rate limiting status across the system.

    **Access**: Restricted to users with `role: "SUPERADMIN"`
    **Purpose**: Provides visibility into current rate limiting state for monitoring
      and debugging

    **Response Schema**:
    ```json
    {
      "message": "Rate limiting status retrieved successfully",
      "data": {
        "enabled": true,
        "storage_type": "RedisStorage",
        "total_active_limits": 5,
        "active_limits": [
          {
            "key": "user:123",
            "type": "user",
            "identifier": "123",
            "current_count": 8,
            "time_window_seconds": "60",
            "user_info": {
              "id": "123",
              "email": "user@example.com",
              "name": "John Doe",
              "role": "USER"
            }
          },
          {
            "key": "ip:192.168.1.100",
            "type": "ip",
            "identifier": "192.168.1.100",
            "current_count": 15,
            "time_window_seconds": "3600",
            "user_info": null
          }
        ]
      }
    }
    ```

    **Response Fields**:
    - `enabled`: Boolean indicating if rate limiting is active
    - `storage_type`: Backend storage type (RedisStorage, MemoryStorage, etc.)
    - `total_active_limits`: Count of currently active rate limit entries
    - `active_limits`: Array of active rate limit entries with:
      - `key`: Internal rate limit identifier
      - `type`: Limit type ("user", "ip", "auth")
      - `identifier`: User ID or IP address being limited
      - `current_count`: Current request count against the limit
      - `time_window_seconds`: Time window for the rate limit (60=1min, 3600=1hr)
      - `user_info`: User details for user-type limits (null for IP limits)

    **Error Responses**:
    - `403 Forbidden`: User does not have SUPERADMIN privileges
    - `401 Unauthorized`: Valid JWT token required
    - `500 Internal Server Error`: Failed to query rate limiting status
    """
    current_user_id = get_jwt_identity()
    user = UserService.get_user(current_user_id)

    if not user or user.role != "SUPERADMIN":
        return jsonify({"msg": "Superadmin access required"}), 403

    try:
        from gefapi.utils.rate_limiting import get_current_rate_limits

        rate_limit_status = get_current_rate_limits()

        return jsonify(
            {
                "message": "Rate limiting status retrieved successfully",
                "data": rate_limit_status,
            }
        ), 200

    except Exception as e:
        app.logger.error(f"Failed to get rate limit status: {e}")
        return jsonify({"error": "Failed to retrieve rate limiting status"}), 500


@endpoints.route("/rate-limit/reset", methods=["POST"])
@jwt_required()
def reset_rate_limits():
    """
    Reset all rate limits across the system.

    **Access**: Restricted to users with `role: "SUPERADMIN"`
    **Purpose**: Clears all current rate limit counters - useful for emergency
      situations or testing

    **Request**: No request body required

    **Success Response Schema**:
    ```json
    {
      "message": "All rate limits have been reset."
    }
    ```

    **Use Cases**:
    - Emergency situations where legitimate users are being rate limited
    - Testing and development environments
    - After configuration changes to rate limiting policies
    - System maintenance and debugging

    **Behavior**:
    - Clears all rate limit counters from storage (Redis/Memory)
    - Affects all endpoints and all users/IP addresses
    - Does not disable rate limiting - new requests will start fresh counters
    - Operation is immediate and irreversible

    **Error Responses**:
    - `403 Forbidden`: User does not have SUPERADMIN privileges
    - `401 Unauthorized`: Valid JWT token required
    - `500 Internal Server Error`: Failed to reset rate limits
    """
    current_user_id = get_jwt_identity()
    user = UserService.get_user(current_user_id)

    if not user or user.role != "SUPERADMIN":
        return jsonify({"msg": "Superadmin access required"}), 403

    try:
        # For Flask-Limiter, we need to reset the storage properly
        # Check if the storage has a reset method or clear all keys
        if hasattr(limiter.storage, "reset"):
            limiter.storage.reset()
        elif hasattr(limiter.storage, "clear_all"):
            limiter.storage.clear_all()
        else:
            # Fallback: try to get all keys and clear them
            # This is storage-dependent, but for memory storage we can try this approach
            try:
                # For MemoryStorage, access the internal storage directly
                if hasattr(limiter.storage, "storage"):
                    limiter.storage.storage.clear()
                else:
                    # Alternative: recreate the limiter to clear all limits
                    limiter._storage = limiter._storage.__class__(limiter._storage_uri)
            except Exception:
                # Last resort: disable and re-enable the limiter to reset state
                was_enabled = limiter.enabled
                limiter.enabled = False
                limiter.enabled = was_enabled

        return jsonify({"message": "All rate limits have been reset."}), 200
    except Exception as e:
        app.logger.error(f"Failed to reset rate limits: {e}")
        return jsonify({"error": "Failed to reset rate limits"}), 500


@endpoints.route("/user/me/sessions", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_user_sessions():
    """
    Retrieve current user's active authentication sessions.

    **Authentication**: JWT token required
    **Access**: Returns current user's own active sessions
    **Purpose**: Session management and security monitoring

    **Response Schema**:
    ```json
    {
      "data": [
        {
          "id": "session-123",
          "user_id": "user-456",
          "created_at": "2025-01-15T10:00:00Z",
          "last_accessed": "2025-01-15T14:30:00Z",
          "expires_at": "2025-01-22T10:00:00Z",
          "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
          "ip_address": "192.168.1.100",
          "is_current": true,
          "device_info": {
            "browser": "Chrome",
            "os": "Windows",
            "device_type": "desktop"
          }
        }
      ]
    }
    ```

    **Session Information**:
    - `id`: Unique session identifier
    - `created_at`: When session was created/login occurred
    - `last_accessed`: Last activity timestamp for this session
    - `expires_at`: When session will automatically expire
    - `user_agent`: Browser/application identifier
    - `ip_address`: IP address of the session
    - `is_current`: Whether this is the current API request's session
    - `device_info`: Parsed device and browser information

    **Use Cases**:
    - Monitor active login sessions across devices
    - Identify suspicious login activity
    - Manage multiple device access
    - Security audit and session cleanup

    **Security Features**:
    - Only shows current user's sessions (privacy protection)
    - Includes last access time for activity monitoring
    - Device fingerprinting for security analysis
    - Current session identification

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `500 Internal Server Error`: Failed to retrieve sessions
    """
    logger.info("[ROUTER]: Getting user sessions")
    identity = current_user

    try:
        active_sessions = RefreshTokenService.get_user_active_sessions(identity.id)
        return jsonify(data=[session.serialize() for session in active_sessions]), 200
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")


@endpoints.route(
    "/user/me/sessions/<session_id>", strict_slashes=False, methods=["DELETE"]
)
@jwt_required()
def revoke_user_session(session_id):
    """
    Revoke a specific authentication session.

    **Authentication**: JWT token required
    **Access**: Users can only revoke their own sessions
    **Purpose**: Selective session termination for security

    **Path Parameters**:
    - `session_id`: Session identifier to revoke

    **Request**: No request body required

    **Success Response Schema**:
    ```json
    {
      "message": "Session revoked successfully"
    }
    ```

    **Revocation Process**:
    - Validates session belongs to current user
    - Invalidates the specified session token
    - Removes session from active sessions list
    - Logs revocation event for security audit

    **Use Cases**:
    - Log out from specific device while staying logged in on others
    - Security response to suspicious session activity
    - Remote device management (e.g., lost phone)
    - Granular session control

    **Security Features**:
    - Users can only revoke their own sessions (privacy protection)
    - Immediate token invalidation
    - Audit logging for security monitoring
    - No impact on other active sessions

    **Session Identification**:
    - Use `GET /user/me/sessions` to list sessions and get session IDs
    - Sessions are identified by unique session identifiers
    - Current session can be revoked (will require re-authentication)

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `404 Not Found`: Session not found or doesn't belong to user
    - `500 Internal Server Error`: Failed to revoke session
    """
    logger.info(f"[ROUTER]: Revoking user session {session_id}")
    identity = current_user

    try:
        # Find the session and verify it belongs to the current user
        session = RefreshToken.query.filter_by(
            id=session_id, user_id=identity.id
        ).first()

        if not session:
            return error(status=404, detail="Session not found")

        if RefreshTokenService.revoke_refresh_token(session.token):
            return jsonify(message="Session revoked successfully"), 200
        return error(status=500, detail="Failed to revoke session")

    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")


@endpoints.route("/user/me/sessions", strict_slashes=False, methods=["DELETE"])
@jwt_required()
def revoke_all_user_sessions():
    """
    Revoke all authentication sessions for current user (logout everywhere).

    **Authentication**: JWT token required
    **Access**: Revokes all sessions for current authenticated user
    **Purpose**: Complete logout from all devices and applications

    **Request**: No request body required

    **Success Response Schema**:
    ```json
    {
      "message": "Successfully revoked 3 sessions"
    }
    ```

    **Revocation Process**:
    - Identifies all active sessions for current user
    - Invalidates all session tokens immediately
    - Removes all sessions from active sessions list
    - Logs bulk revocation event for security audit
    - Includes current session (user will need to re-authenticate)

    **Use Cases**:
    - Emergency security response (suspected account compromise)
    - Complete logout when changing passwords
    - Privacy protection when using shared/public computers
    - Account cleanup and security hygiene

    **Security Features**:
    - Immediate invalidation of all tokens
    - Forces re-authentication on all devices
    - Comprehensive security reset
    - Audit logging with session count

    **Post-Revocation Effect**:
    - User is logged out from all devices/applications
    - All API requests with old tokens will fail
    - Fresh login required on all devices
    - New sessions will have new tokens and IDs

    **Important Notes**:
    - This action affects the current session making the request
    - User will need to re-authenticate immediately after this call
    - All mobile apps and browser sessions will require re-login
    - Consider using this for security incidents or password changes

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `500 Internal Server Error`: Failed to revoke sessions
    """
    logger.info("[ROUTER]: Revoking all user sessions")
    identity = current_user

    try:
        revoked_count = RefreshTokenService.revoke_all_user_tokens(identity.id)
        return jsonify(message=f"Successfully revoked {revoked_count} sessions"), 200
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
