"""Shared account profile updates for OIDC user tokens.

``PATCH /api/v1/account/profile`` lets the caller update the shared
``name``/``institution`` fields that every application sees.  It is global, not
application-scoped: the response carries identity fields only and never an
application grant or role.  The caller must present an RS256 OIDC access token
with the ``account:write`` scope (granted and still registered for the client),
a verified email, and, when the client is bound to an application, a current
effective grant for that application.  Legacy HS256 and service tokens are
rejected.
"""

from flask import g, jsonify, request

from gefapi.errors import UserNotFound
from gefapi.routes.api.v1 import endpoints
from gefapi.services.user_service import UserService
from gefapi.utils.app_access import app_access_status, app_label, has_app_access
from gefapi.utils.bearer_auth import bearer_required
from gefapi.utils.oidc_scopes import SCOPE_ACCOUNT_WRITE, require_oidc_scopes
from gefapi.validators import validate_institution, validate_name

PROFILE_FIELDS = frozenset({"name", "institution"})


def _denied(status, error, detail, **extra):
    return jsonify(status=status, error=error, detail=detail, **extra), status


def serialize_identity(user):
    """Return identity/profile fields only (no application authority)."""
    return {
        "sub": str(user.id),
        "email": user.email,
        "email_verified": bool(user.email_verified),
        "name": user.name,
        "institution": user.institution,
        "active": bool(user.is_active),
    }


def _account_checks():
    client = g.get("oidc_client")
    if client is None:
        return _denied(
            403,
            "client_identity_required",
            "This endpoint requires an application access token",
        )
    user = g.oidc_user
    app_key = client.required_app_key
    if app_key and not has_app_access(user, app_key):
        return _denied(
            403,
            "app_access_required",
            f"Access to {app_label(app_key)} is not activated",
            app_key=app_key,
            app_access=app_access_status(user, app_key),
        )
    return None


@endpoints.route("/account/profile", strict_slashes=False, methods=["PATCH"])
@bearer_required()
def account_update_profile():
    """Update the caller's shared ``name`` and/or ``institution`` only."""
    denied = _account_checks()
    if denied is not None:
        return denied
    return _update_profile()


@require_oidc_scopes(SCOPE_ACCOUNT_WRITE)
def _update_profile():
    user = g.oidc_user
    if not user.email_verified:
        return _denied(403, "email_unverified", "Email address is not verified")
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body:
        return _denied(400, "invalid_request", "A JSON object body is required")
    disallowed = sorted(set(body) - PROFILE_FIELDS)
    if disallowed:
        return _denied(
            400,
            "field_not_allowed",
            "Only name and institution may be updated",
            fields=disallowed,
        )
    update = {}
    try:
        if "name" in body:
            if not isinstance(body["name"], str):
                raise ValueError("Name must be a string")
            update["name"] = validate_name(body["name"])
        if "institution" in body:
            institution = body["institution"]
            if institution is not None and not isinstance(institution, str):
                raise ValueError("Institution must be a string")
            update["institution"] = validate_institution(institution) or None
    except ValueError as exc:
        return _denied(400, "invalid_profile", str(exc))
    try:
        updated = UserService.update_user(update, str(user.id))
    except UserNotFound:
        return _denied(404, "not_found", "User not found")
    return jsonify(data=serialize_identity(updated)), 200
