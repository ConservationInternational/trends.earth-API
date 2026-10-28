"""OAuth2 scope definitions and enforcement for service clients.

Scopes restrict what a client-credentials token can do.  An empty scope
string (or the special ``all`` scope) grants full access — i.e. the
client inherits every permission of the owning user.

Scope enforcement is intentionally limited to tokens issued via the
``client_credentials`` grant.  Regular user JWT tokens are unaffected.
"""

import functools
import logging

from flask import g, jsonify
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
        "client:manage",
        "admin:read",
        "admin:write",
    }
)

PRIVILEGED_SERVICE_CLIENT_SCOPES = frozenset(
    {
        "client:manage",
        "admin:read",
        "admin:write",
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
    "client:manage": "List and revoke service clients",
    "admin:read": "View administrative monitoring data",
    "admin:write": "Change administrative settings and manage other users",
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


def requested_scopes(scopes_string: str) -> set[str]:
    """Return normalised requested scopes from a space-delimited string."""
    return set((scopes_string or "").strip().split())


def has_privileged_service_client_scope(scopes_string: str) -> bool:
    """Return True when scopes include administrative/user-management powers."""
    return bool(requested_scopes(scopes_string) & PRIVILEGED_SERVICE_CLIENT_SCOPES)


def is_service_client_token(jwt_claims: dict | None) -> bool:
    """Return True when *jwt_claims* come from the client_credentials grant."""
    return bool(jwt_claims) and jwt_claims.get("grant_type") == "client_credentials"


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
            # OIDC user tokens (accepted via bearer_required) must carry the
            # scope and their client must still be registered for it.  Legacy
            # and service-client tokens never reach this branch.
            oidc_client = g.get("oidc_client")
            # Only trust the OIDC slots when they describe the token verified
            # for this request (a legacy jwt_required route never sets them).
            if oidc_client is not None and g.get("_jwt_extended_jwt") is g.get(
                "oidc_claims"
            ):
                from gefapi.utils.oidc_scopes import effective_token_scopes

                if scope not in effective_token_scopes():
                    logger.warning(
                        "Scope '%s' denied for OIDC client %s",
                        scope,
                        oidc_client.client_id,
                    )
                    return (
                        jsonify(
                            {
                                "status": 403,
                                "detail": f"Insufficient scope. Required: {scope}",
                            }
                        ),
                        403,
                    )
                # Re-check the live application grant: revoking access must
                # take effect before already-issued tokens expire.
                app_key = oidc_client.required_app_key
                if app_key:
                    from flask_jwt_extended import current_user

                    from gefapi.utils.app_access import (
                        app_access_status,
                        has_app_access,
                    )

                    if not has_app_access(current_user, app_key):
                        return (
                            jsonify(
                                {
                                    "status": 403,
                                    "detail": "Application access is not active",
                                    "error": "app_access_required",
                                    "app_key": app_key,
                                    "app_access": app_access_status(
                                        current_user, app_key
                                    ),
                                }
                            ),
                            403,
                        )
                return fn(*args, **kwargs)

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
