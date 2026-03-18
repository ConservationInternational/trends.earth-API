"""Admin routes for rate limiting and session management."""

from datetime import UTC, datetime, timedelta
import logging

from flask import jsonify, request
from flask_jwt_extended import current_user, get_jwt_identity, jwt_required

from gefapi import app, limiter
from gefapi.models.refresh_token import RefreshToken
from gefapi.routes.api.v1 import endpoints, error
from gefapi.services import (
    ClientStatsService,
    ClientTrackingService,
    RateLimitEventService,
    UserService,
)
from gefapi.services.refresh_token_service import RefreshTokenService

logger = logging.getLogger()


@endpoints.route("/rate-limit/status", methods=["GET"])
@jwt_required()
def get_rate_limit_status():
    """
    Query current rate limiting status across the system.

    **Access**: Restricted to users with `role: "SUPERADMIN"`
    **Purpose**: Provides visibility into current rate limiting state for monitoring
      and debugging.

    **Response Schema**:
    ```json
    {
      "message": "Rate limiting status retrieved successfully",
      "data": {
        "enabled": true,
        "storage_type": "database",
        "total_active_limits": 1,
        "active_limits": [
          {
            "key": "user:123",
            "identifier": "user:123",
            "type": "user",
            "occurred_at": "2025-11-02T10:15:30+00:00",
            "expires_at": "2025-11-02T10:16:30+00:00",
            "limit_definition": "5 per minute",
            "limit": 5,
            "current_count": null,
            "time_window_seconds": 60,
            "retry_after_seconds": 60,
            "user_info": {
              "id": "123",
              "email": "user@example.com",
              "name": "John Doe",
              "role": "USER"
            }
          }
        ]
      }
    }
    ```

    **Response Fields**:
    - `enabled`: Whether rate limiting is active.
        - `storage_type`: Backend storage type reported by the status endpoint.
        - `total_active_limits`: Count of currently active rate limit entries.
        - `active_limits`: Array with metadata for each active rate limit, including:
      - `key`: Identifier used by the limiter storage (e.g., `user:<uuid>`).
      - `identifier`: User or IP identifier associated with the limit.
      - `type`: Limit category such as `user`, `ip`, or `auth`.
      - `occurred_at`: Timestamp when the most recent 429 was recorded.
      - `expires_at`: Timestamp when the limit will automatically expire.
      - `limit_definition`: Human-readable description of the breached rule.
            - `limit`: Parsed numeric request limit when available.
      - `current_count`: Latest known request count (may be null).
      - `time_window_seconds`: Time window for the limit in seconds.
      - `retry_after_seconds`: Retry hint provided by the limiter, if available.
            - `user_info`: Contextual user information for user-type limits
                (null otherwise).

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

    except Exception as exc:  # pragma: no cover - defensive logging
        app.logger.error(f"Failed to get rate limit status: {exc}")
        return jsonify({"error": "Failed to retrieve rate limiting status"}), 500


@endpoints.route("/rate-limit/reset/<string:identifier>", methods=["POST"])
@jwt_required()
def reset_rate_limit_by_identifier(identifier):
    """
    Reset a specific rate limit by its identifier.

    **Access**: Restricted to users with `role: "SUPERADMIN"`
    **Purpose**: Clears rate limit counters for a specific user or IP address

    **Path Parameters**:
    - `identifier`: The rate limit identifier (e.g., "user:123", "ip:192.168.1.1",
      "auth:hash:ip")

    **Success Response Schema**:
    ```json
    {
      "message": "Rate limit reset for identifier: user:123"
    }
    ```

    **Use Cases**:
    - Clear rate limit for specific user who was legitimately rate limited
    - Remove rate limit for specific IP address
    - Targeted rate limit management without affecting all users

    **Error Responses**:
    - `403 Forbidden`: User does not have SUPERADMIN privileges
    - `401 Unauthorized`: Valid JWT token required
    - `404 Not Found`: No rate limit found for the specified identifier
    - `500 Internal Server Error`: Failed to reset rate limit
    """
    current_user_id = get_jwt_identity()
    user = UserService.get_user(current_user_id)

    if not user or user.role != "SUPERADMIN":
        return jsonify({"msg": "Superadmin access required"}), 403

    try:
        from gefapi.utils.rate_limiting import reset_rate_limit_by_key

        success = reset_rate_limit_by_key(identifier)

        if success:
            return jsonify(
                {"message": f"Rate limit reset for identifier: {identifier}"}
            ), 200
        return jsonify(
            {"error": f"No rate limit found for identifier: {identifier}"}
        ), 404

    except Exception as e:
        app.logger.error(f"Failed to reset rate limit for {identifier}: {e}")
        return jsonify({"error": "Failed to reset rate limit"}), 500


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

        expired_events = RateLimitEventService.expire_all_active_events()
        if expired_events:
            app.logger.info(
                "Expired %s active rate limit events during bulk reset",
                expired_events,
            )

        return jsonify({"message": "All rate limits have been reset."}), 200
    except Exception as e:
        app.logger.error(f"Failed to reset rate limits: {e}")
        return jsonify({"error": "Failed to reset rate limits"}), 500


@endpoints.route("/rate-limit/events", methods=["GET"])
@jwt_required()
def get_rate_limit_events():
    """Retrieve historical rate limit breach events for auditing."""

    current_user_id = get_jwt_identity()
    user = UserService.get_user(current_user_id)

    if not user or user.role not in ("ADMIN", "SUPERADMIN"):
        return jsonify({"msg": "Admin or superadmin access required"}), 403

    try:
        page = max(request.args.get("page", default=1, type=int) or 1, 1)
        per_page = request.args.get("per_page", default=100, type=int) or 100
        per_page = max(1, min(per_page, 500))
        offset = (page - 1) * per_page

        since = None
        since_param = request.args.get("since")
        if since_param:
            try:
                parsed = datetime.fromisoformat(since_param)
                since = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            except ValueError:
                app.logger.warning(
                    "Invalid 'since' parameter supplied to rate limit events: %s",
                    since_param,
                )

        if since is None:
            since_hours = request.args.get("since_hours", type=int)
            if since_hours is not None:
                since = datetime.now(UTC) - timedelta(hours=max(since_hours, 0))

        if since is None:
            since_days = request.args.get("since_days", type=int)
            if since_days is not None:
                since = datetime.now(UTC) - timedelta(days=max(since_days, 0))

        rate_limit_type = request.args.get("type")
        user_filter = request.args.get("user_id")
        ip_filter = request.args.get("ip")
        sort_param = request.args.get("sort")
        filter_param = request.args.get("filter")
        status_filter = request.args.get("status")

        events, total = RateLimitEventService.list_events(
            limit=per_page,
            offset=offset,
            since=since,
            rate_limit_type=rate_limit_type,
            user_id=user_filter,
            ip_address=ip_filter,
            sort=sort_param,
            filter_param=filter_param,
            status=status_filter,
        )

        total_pages = (total + per_page - 1) // per_page if per_page else 0

        data = [event.serialize() for event in events]

        return (
            jsonify(
                {
                    "data": data,
                    "total": total,
                    "page": page,
                    "per_page": per_page,
                    "total_pages": total_pages,
                }
            ),
            200,
        )
    except Exception as exc:  # pragma: no cover - defensive logging path
        app.logger.error(f"Failed to retrieve rate limit events: {exc}")
        return jsonify({"error": "Failed to retrieve rate limit events"}), 500


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


# =============================================================================
# Client Platform Tracking Endpoints
# =============================================================================


@endpoints.route("/user/me/clients", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_user_clients():
    """
    Get current user's client platform metadata.

    **Authentication**: JWT token required
    **Purpose**: Shows which client platforms the user has accessed the API from

    **Response Schema**:
    ```json
    {
      "data": [
        {
          "id": "uuid",
          "client_type": "qgis_plugin",
          "client_version": "2.2.4",
          "os": "Windows",
          "qgis_version": "3.34.0",
          "last_seen_at": "2026-03-18T12:00:00Z",
          "created_at": "2026-01-15T10:30:00Z"
        }
      ]
    }
    ```

    **Client Types**:
    - `qgis_plugin`: Trends.Earth QGIS plugin
    - `api_ui`: Web admin interface
    - `cli`: Command-line interface

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `500 Internal Server Error`: Failed to retrieve clients
    """
    identity = current_user
    logger.info(f"[ROUTER]: Getting client metadata for user {identity.id}")

    try:
        clients = ClientTrackingService.get_user_clients(identity.id)
        return jsonify(data=[c.serialize() for c in clients]), 200
    except Exception as e:
        logger.error(f"[ROUTER]: Error getting user clients: {e}")
        return error(status=500, detail="Failed to retrieve client metadata")


@endpoints.route("/admin/client-stats", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_client_stats():
    """
    Get aggregated client platform usage statistics.

    **Access**: Restricted to users with `role: "SUPERADMIN"`
    **Purpose**: Provides visibility into client platform and version usage

    **Query Parameters**:
    - `days`: Time period in days (7, 14, 30, 60, 90). Default: 30
    - `client_type`: Optional filter (qgis_plugin, api_ui, cli)

    **Response Schema**:
    ```json
    {
      "period_days": 30,
      "generated_at": "2026-03-18T12:00:00Z",
      "platform_summary": {
        "qgis_plugin": {"active_users": 450, "total_users": 800},
        "api_ui": {"active_users": 50, "total_users": 120}
      },
      "plugin_stats": {
        "by_plugin_version": [
          {
            "version": "2.2.4",
            "user_count": 450,
            "by_qgis_version": [
              {"qgis_version": "3.34", "count": 300}
            ],
            "by_os": [
              {"os": "Windows", "count": 280}
            ]
          }
        ],
        "by_qgis_version": [
          {
            "qgis_version": "3.34",
            "user_count": 500,
            "by_plugin_version": [
              {"version": "2.2.4", "count": 300}
            ]
          }
        ],
        "by_os": [
          {"os": "Windows", "user_count": 520}
        ]
      },
      "api_ui_stats": {
        "by_version": [{"version": "1.5.2", "user_count": 35}]
      },
      "cli_stats": {
        "by_version": [{"version": "1.0.0", "user_count": 10}]
      }
    }
    ```

    **Use Cases**:
    - Monitor plugin version adoption
    - Plan deprecation of old versions
    - Understand platform and OS distribution
    - Inform release decisions

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Superadmin access required
    - `500 Internal Server Error`: Failed to compute statistics
    """
    current_user_id = get_jwt_identity()
    user = UserService.get_user(current_user_id)

    if not user or user.role != "SUPERADMIN":
        return jsonify({"msg": "Superadmin access required"}), 403

    # Parse query parameters
    days = request.args.get("days", 30, type=int)
    client_type = request.args.get("client_type", None, type=str)

    logger.info(f"[ROUTER]: Getting client stats (days={days}, type={client_type})")

    try:
        stats = ClientStatsService.get_client_stats(days=days, client_type=client_type)
        return jsonify(stats), 200
    except Exception as e:
        logger.error(f"[ROUTER]: Error getting client stats: {e}")
        return error(status=500, detail="Failed to compute client statistics")
