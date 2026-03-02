"""OAuth2 scope definitions and enforcement for service clients.

Scopes restrict what a client-credentials token can do.  An empty scope
string (or the special ``all`` scope) grants full access — i.e. the
client inherits every permission of the owning user.

Scope enforcement is intentionally limited to tokens issued via the
``client_credentials`` grant.  Regular user JWT tokens are unaffected.
"""

import functools
import logging

from flask import jsonify
from flask_jwt_extended import get_jwt

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Recognised scopes
# ------------------------------------------------------------------

VALID_SCOPES = frozenset(
    {
        "all",
        "execution:read",
        "execution:write",
        "script:read",
        "script:write",
        "user:read",
        "user:write",
        "boundary:read",
        "gee:read",
        "gee:write",
        "stats:read",
    }
)

# Human-friendly labels (useful for documentation / UI)
SCOPE_DESCRIPTIONS = {
    "all": "Full access (all permissions)",
    "execution:read": "View executions, logs, and results",
    "execution:write": "Run, update, and cancel executions",
    "script:read": "View and download scripts",
    "script:write": "Create, update, publish, and delete scripts",
    "user:read": "View user profile information",
    "user:write": "Update profile and change password",
    "boundary:read": "View administrative boundary data",
    "gee:read": "View Google Earth Engine credential status",
    "gee:write": "Manage Google Earth Engine credentials",
    "stats:read": "View dashboard and system statistics",
}


def validate_scopes(scopes_string: str) -> str | None:
    """Validate a space-delimited scope string.

    Returns
    -------
    str | None
        ``None`` if all scopes are valid; otherwise a human-readable
        error message listing the invalid scopes.
    """
    if not scopes_string or not scopes_string.strip():
        return None  # empty = full access, always valid

    requested = set(scopes_string.strip().split())
    invalid = requested - VALID_SCOPES
    if invalid:
        return (
            f"Invalid scope(s): {', '.join(sorted(invalid))}. "
            f"Valid scopes: {', '.join(sorted(VALID_SCOPES))}"
        )

    if "all" in requested and len(requested) > 1:
        return (
            "The 'all' scope grants full access and cannot be combined "
            "with other scopes. Use 'all' alone, or list only the "
            "specific scopes you need."
        )

    return None


def _has_scope(required_scope: str, jwt_claims: dict) -> bool:
    """Check whether *jwt_claims* satisfy *required_scope*.

    Rules:
    * Non-client_credentials tokens always pass (scopes only apply to
      service-client tokens).
    * Empty scope string or ``"all"`` in the scope list ⇒ full access.
    * Otherwise the required scope must appear in the space-delimited
      list.
    """
    grant_type = jwt_claims.get("grant_type")
    if grant_type != "client_credentials":
        return True  # regular user tokens are unrestricted

    scopes_str = jwt_claims.get("scopes", "")
    if not scopes_str:
        return True  # empty = full access

    scope_set = set(scopes_str.split())
    if "all" in scope_set:
        return True

    return required_scope in scope_set


def require_scope(scope: str):
    """Decorator that enforces an OAuth2 scope on a Flask route.

    Must be applied **after** ``@jwt_required()`` so that the JWT is
    already verified when this decorator runs.

    Usage::

        @endpoints.route("/some/path", methods=["GET"])
        @jwt_required()
        @require_scope("execution:read")
        def my_view():
            ...
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # jwt_required() has already run; just read the claims.
            claims = get_jwt()
            if not _has_scope(scope, claims):
                logger.warning(
                    "Scope '%s' denied for client_credentials token (scopes=%s)",
                    scope,
                    claims.get("scopes", ""),
                )
                return (
                    jsonify(
                        {
                            "status": 403,
                            "detail": (f"Insufficient scope. Required: {scope}"),
                        }
                    ),
                    403,
                )
            return fn(*args, **kwargs)

        return wrapper

    return decorator
