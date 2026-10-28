"""OIDC user-token scopes for the shared account and application APIs.

These are OAuth ``scope`` values carried by RS256 OIDC access tokens.  They are
unrelated to the service-client ``scopes`` claim in :mod:`gefapi.utils.scopes`.

A scope only permits an operation category: it is checked against the
intersection of the scopes granted to the token *and* the scopes currently
registered for the OAuth client, so removing a scope from a client takes effect
immediately for already-issued tokens.  Roles and grants are always checked
separately against the database.
"""

import functools

from flask import g, jsonify

SCOPE_OPENID = "openid"
SCOPE_EMAIL = "email"
SCOPE_PROFILE = "profile"
SCOPE_APP_READ = "app:read"
SCOPE_ACCOUNT_WRITE = "account:write"
SCOPE_APP_USERS_READ = "app-users:read"
SCOPE_APP_ACCESS_WRITE = "app-access:write"

# Opt-in scopes letting a user token call the shared script/execution routes.
# Named like the service-client scopes they mirror; never granted by default.
SCOPE_SCRIPT_READ = "script:read"
SCOPE_EXECUTION_READ = "execution:read"
SCOPE_EXECUTION_WRITE = "execution:write"

BASE_SCOPES = (SCOPE_OPENID, SCOPE_EMAIL, SCOPE_PROFILE)
EXECUTION_SCOPES = (SCOPE_SCRIPT_READ, SCOPE_EXECUTION_READ, SCOPE_EXECUTION_WRITE)
# Scopes that only make sense for a client bound to an application.
APP_SCOPES = (SCOPE_APP_READ, SCOPE_APP_USERS_READ, SCOPE_APP_ACCESS_WRITE)
ACCOUNT_SCOPES = (SCOPE_ACCOUNT_WRITE,)
SUPPORTED_OIDC_SCOPES = (
    BASE_SCOPES
    + (SCOPE_APP_READ, SCOPE_ACCOUNT_WRITE)
    + (
        SCOPE_APP_USERS_READ,
        SCOPE_APP_ACCESS_WRITE,
    )
    + EXECUTION_SCOPES
)

# Registration defaults.  Existing clients are never changed automatically.
DEFAULT_CLIENT_SCOPES = BASE_SCOPES
DEFAULT_APP_CLIENT_SCOPES = BASE_SCOPES + (
    SCOPE_APP_READ,
    SCOPE_ACCOUNT_WRITE,
    SCOPE_APP_USERS_READ,
    SCOPE_APP_ACCESS_WRITE,
)


def default_client_scopes(required_app_key):
    """Return the default scope list for a newly registered client."""
    return list(
        DEFAULT_APP_CLIENT_SCOPES if required_app_key else DEFAULT_CLIENT_SCOPES
    )


def client_scope_error(scopes, required_app_key):
    """Return an error message when *scopes* are not valid for a client."""
    scopes = list(scopes)
    unknown = sorted(set(scopes) - set(SUPPORTED_OIDC_SCOPES))
    if unknown:
        return f"Unsupported scope(s): {', '.join(unknown)}"
    if SCOPE_OPENID not in scopes:
        return "openid scope is required"
    if not required_app_key:
        app_only = sorted(set(scopes) & set(APP_SCOPES))
        if app_only:
            return (
                f"Scope(s) {', '.join(app_only)} require a client with a "
                "required_app_key"
            )
    return None


def effective_token_scopes():
    """Return granted token scopes that the client is still registered for."""
    client = g.get("oidc_client") if g else None
    if client is None:
        return frozenset()
    claims = g.get("oidc_claims") or {}
    granted = set((claims.get("scope") or "").split())
    registered = set((client.scopes or "").split())
    return frozenset(granted & registered)


def require_oidc_scopes(*scopes):
    """Decorator requiring :data:`BASE_SCOPES` plus *scopes* on an OIDC token.

    Apply after an OIDC client-identity check; callers without a client (legacy
    HS256 or service tokens) never satisfy it.
    """
    required = frozenset(BASE_SCOPES + scopes)

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            missing = sorted(required - effective_token_scopes())
            if missing:
                return jsonify(
                    status=403,
                    error="insufficient_scope",
                    detail="The access token is missing required scope(s)",
                    required_scopes=sorted(required),
                    missing_scopes=missing,
                ), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator
