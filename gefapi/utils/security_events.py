"""Security event logging utilities for the GEF API"""

from datetime import datetime
import logging
from typing import Any, Optional

from flask import has_request_context, request
from flask_limiter.util import get_remote_address
import rollbar

logger = logging.getLogger(__name__)

# Security event types for consistent logging
SECURITY_EVENTS = {
    "LOGIN_SUCCESS": "User login successful",
    "LOGIN_FAILURE": "User login failed",
    "PASSWORD_CHANGE": "User password changed",
    "PASSWORD_RESET": "Password reset requested",
    "ADMIN_ACTION": "Administrative action performed",
    "RATE_LIMIT_HIT": "Rate limit exceeded",
    "SUSPICIOUS_ACTIVITY": "Suspicious activity detected",
    "SESSION_INVALIDATED": "User session invalidated",
    "ROLE_CHANGE": "User role changed",
    "ACCOUNT_LOCKED": "User account locked",
    "UNAUTHORIZED_ACCESS": "Unauthorized access attempt",
    "DATA_EXPORT": "Sensitive data exported",
    "SCRIPT_EXECUTION": "Script execution started",
    "API_KEY_USED": "API key authentication used",
}


def log_security_event(
    event_type: str,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    level: str = "warning",
) -> None:
    """
    Centralized security event logging function.

    Args:
        event_type: Type of security event (should be from SECURITY_EVENTS)
        user_id: ID of the user involved (if applicable)
        user_email: Email of the user involved (if applicable)
        details: Additional details about the event
        level: Log level ('info', 'warning', 'error')
    """
    if event_type not in SECURITY_EVENTS:
        logger.warning(f"Unknown security event type: {event_type}")

    # Gather request context if available
    request_data = {}
    if has_request_context():
        try:
            request_data = {
                "ip_address": get_remote_address(),
                "user_agent": request.headers.get("User-Agent", "Unknown"),
                "endpoint": request.endpoint,
                "method": request.method,
                "url": request.url,
                "path": request.path,
            }
        except Exception as e:
            logger.debug(f"Failed to gather request context: {e}")

    # Build event data
    event_data = {
        "event_type": event_type,
        "event_description": SECURITY_EVENTS.get(event_type, "Unknown security event"),
        "timestamp": datetime.utcnow().isoformat(),
        "user_id": user_id,
        "user_email": user_email,
        "details": details or {},
        "request_info": request_data,
    }

    # Filter out None values for cleaner logs
    event_data = {k: v for k, v in event_data.items() if v is not None}

    # Log locally
    log_message = f"SECURITY_EVENT: {event_type}"
    if user_email:
        log_message += f" - User: {user_email}"
    if details:
        log_message += f" - Details: {details}"

    getattr(logger, level)(log_message, extra=event_data)

    # Send to Rollbar for centralized monitoring
    try:
        rollbar_level = "info" if level == "info" else "warning"
        rollbar.report_message(
            message=f"Security Event: {event_type}",
            level=rollbar_level,
            extra_data=event_data,
        )
    except Exception as e:
        logger.error(f"Failed to send security event to Rollbar: {e}")


def log_authentication_event(
    success: bool, email: str, reason: Optional[str] = None
) -> None:
    """
    Convenience function for logging authentication events.

    Args:
        success: Whether authentication was successful
        email: Email address of the user
        reason: Reason for failure (if applicable)
    """
    if success:
        log_security_event("LOGIN_SUCCESS", user_email=email, level="info")
    else:
        log_security_event(
            "LOGIN_FAILURE",
            user_email=email,
            details={"reason": reason},
            level="warning",
        )


def log_admin_action(
    admin_user_id: str,
    admin_email: str,
    action: str,
    target_user_id: Optional[str] = None,
) -> None:
    """
    Log administrative actions for audit trail.

    Args:
        admin_user_id: ID of the admin performing the action
        admin_email: Email of the admin performing the action
        action: Description of the action performed
        target_user_id: ID of the user being acted upon (if applicable)
    """
    log_security_event(
        "ADMIN_ACTION",
        user_id=admin_user_id,
        user_email=admin_email,
        details={"action": action, "target_user_id": target_user_id},
        level="info",
    )


def log_suspicious_activity(
    description: str, user_id: Optional[str] = None, user_email: Optional[str] = None
) -> None:
    """
    Log suspicious activity that may require investigation.

    Args:
        description: Description of the suspicious activity
        user_id: ID of the user involved (if known)
        user_email: Email of the user involved (if known)
    """
    log_security_event(
        "SUSPICIOUS_ACTIVITY",
        user_id=user_id,
        user_email=user_email,
        details={"description": description},
        level="warning",
    )


def log_rate_limit_exceeded(limit_type: str, user_id: Optional[str] = None) -> None:
    """
    Log rate limit violations.

    Args:
        limit_type: Type of rate limit that was exceeded
        user_id: ID of the user (if authenticated)
    """
    log_security_event(
        "RATE_LIMIT_HIT",
        user_id=user_id,
        details={"limit_type": limit_type},
        level="warning",
    )


def log_password_event(
    event_type: str, user_id: str, user_email: str, admin_action: bool = False
) -> None:
    """
    Log password-related security events.

    Args:
        event_type: 'PASSWORD_CHANGE' or 'PASSWORD_RESET'
        user_id: ID of the user whose password was changed
        user_email: Email of the user whose password was changed
        admin_action: Whether this was performed by an admin
    """
    log_security_event(
        event_type,
        user_id=user_id,
        user_email=user_email,
        details={"admin_action": admin_action},
        level="info",
    )
