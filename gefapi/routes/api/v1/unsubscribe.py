"""Public unsubscribe endpoints for managing bulk email subscription preferences.

These endpoints do NOT require authentication — they accept a signed JWT
unsubscribe token (generated per-recipient at send time and embedded in each
email's footer link).

Token payload: {"sub": "<user_id>", "purpose": "unsubscribe", "exp": <timestamp>}
"""

import logging
import uuid

from flask import jsonify, request
import jwt as pyjwt

from gefapi import db, limiter
from gefapi.config import SETTINGS
from gefapi.models import User
from gefapi.routes.api.v1 import endpoints, error
from gefapi.utils.rate_limiting import (
    RateLimitConfig,
    get_non_exempt_key,
    is_rate_limiting_disabled,
)

logger = logging.getLogger(__name__)

_VALID_SUBSCRIPTION_FIELDS = {
    "automated": "email_notifications_enabled",
    "news": "email_subscription_news",
    "engagement": "email_subscription_engagement",
    "system_updates": "email_subscription_system_updates",
}


def _decode_unsubscribe_token(token):
    """Decode and validate an unsubscribe JWT.

    Returns (user_id_str, error_response) — exactly one will be non-None.
    Uses UNSUBSCRIBE_JWT_SECRET when set (falling back to JWT_SECRET_KEY) so
    that the two token lifecycles remain independent.
    """
    secret = SETTINGS.get("UNSUBSCRIBE_JWT_SECRET") or SETTINGS.get("JWT_SECRET_KEY")
    if not secret:
        return None, error(500, "Server misconfiguration: JWT secret not set.")
    try:
        payload = pyjwt.decode(token, secret, algorithms=["HS256"])
    except pyjwt.ExpiredSignatureError:
        return None, error(400, "Unsubscribe link has expired. Please contact support.")
    except pyjwt.InvalidTokenError:
        return None, error(400, "Invalid or malformed unsubscribe token.")

    if payload.get("purpose") != "unsubscribe":
        return None, error(400, "Invalid token purpose.")

    user_id = payload.get("sub")
    if not user_id:
        return None, error(400, "Token missing user identifier.")

    return user_id, None


def _get_user_by_id(user_id):
    """Return (user, error_response). Validates that user_id is a valid UUID."""
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, AttributeError):
        return None, error(400, "Invalid user identifier in token.")
    user = User.query.get(uid)
    if user is None:
        return None, error(404, "User not found.")
    return user, None


@endpoints.route("/unsubscribe", methods=["GET"])
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_user_creation_limits()) or "30 per minute",
    key_func=get_non_exempt_key,
    exempt_when=is_rate_limiting_disabled,
)
def get_unsubscribe_prefs():
    """Return the current email subscription preferences for a tokenised user.

    Query params:
        token: signed JWT unsubscribe token (required)
    """
    token = request.args.get("token")
    if not token:
        return error(400, "token query parameter is required.")

    user_id, err = _decode_unsubscribe_token(token)
    if err:
        return err

    user, err = _get_user_by_id(user_id)
    if err:
        return err

    return (
        jsonify(
            {
                "data": {
                    "automated": getattr(user, "email_notifications_enabled", True),
                    "news": getattr(user, "email_subscription_news", True),
                    "engagement": getattr(user, "email_subscription_engagement", True),
                    "system_updates": getattr(
                        user, "email_subscription_system_updates", True
                    ),
                }
            }
        ),
        200,
    )


@endpoints.route("/unsubscribe", methods=["PATCH"])
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_user_creation_limits()) or "30 per minute",
    key_func=get_non_exempt_key,
    exempt_when=is_rate_limiting_disabled,
)
def update_unsubscribe_prefs():
    """Update the email subscription preferences for a tokenised user.

    Query params:
        token: signed JWT unsubscribe token (required)

    Body (JSON, all fields optional):
        {"news": bool, "engagement": bool, "system_updates": bool}
    """
    token = request.args.get("token")
    if not token:
        return error(400, "token query parameter is required.")

    user_id, err = _decode_unsubscribe_token(token)
    if err:
        return err

    user, err = _get_user_by_id(user_id)
    if err:
        return err

    body = request.get_json(force=True, silent=True) or {}
    updated = False
    for field_key, attr_name in _VALID_SUBSCRIPTION_FIELDS.items():
        if field_key in body:
            value = body[field_key]
            if isinstance(value, bool):
                setattr(user, attr_name, value)
                updated = True

    if not updated:
        return error(400, "No valid subscription fields provided.")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Error updating subscription preferences for user %s", user_id)
        return error(500, "Failed to update preferences. Please try again.")

    return (
        jsonify(
            {
                "data": {
                    "automated": user.email_notifications_enabled,
                    "news": user.email_subscription_news,
                    "engagement": user.email_subscription_engagement,
                    "system_updates": user.email_subscription_system_updates,
                },
                "message": "Subscription preferences updated.",
            }
        ),
        200,
    )
