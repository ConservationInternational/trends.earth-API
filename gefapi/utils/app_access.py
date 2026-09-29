"""Per-application access control for first-party Trends.Earth applications.

The API is the single identity provider for several first-party applications.
Some of them are *gated*: a user may only sign in once an administrator has
activated their access.  Gating is declared per OAuth client via
``OAuthClient.required_app_key``, and each user's access is recorded in the
``user_app_access`` table.

Absence of a grant row means **no access**.  Nothing here ever default-allows.
"""

import functools
import logging

from flask import g, jsonify
from flask_jwt_extended import current_user

from gefapi.models.app_access import (
    DEFAULT_APP_ROLE,
    STATUS_ACTIVE,
    UserAppAccess,
)
from gefapi.utils.permissions import is_superadmin

logger = logging.getLogger(__name__)

APP_AVOIDED_EMISSIONS = "avoided_emissions"
APP_RIO_COHERENCE = "rio_coherence"

APP_KEYS = frozenset({APP_AVOIDED_EMISSIONS, APP_RIO_COHERENCE})

APP_LABELS = {
    APP_AVOIDED_EMISSIONS: "Avoided Emissions",
    APP_RIO_COHERENCE: "Rio Coherence",
}

# Per-application role vocabularies.  These replace each application's own
# local role model so that no application needs a local user store.
APP_ROLES = {
    APP_AVOIDED_EMISSIONS: ("member", "admin"),
    APP_RIO_COHERENCE: ("member", "admin"),
}


def valid_app_key(app_key):
    """Return True when *app_key* names a known gated application."""
    return app_key in APP_KEYS


def app_label(app_key):
    """Return the human-readable label for *app_key*."""
    return APP_LABELS.get(app_key, app_key)


def valid_app_role(app_key, role):
    """Return True when *role* is part of *app_key*'s role vocabulary."""
    return role in APP_ROLES.get(app_key, ())


def default_role(app_key):
    """Return the default role granted for *app_key*."""
    roles = APP_ROLES.get(app_key, (DEFAULT_APP_ROLE,))
    return DEFAULT_APP_ROLE if DEFAULT_APP_ROLE in roles else roles[0]


def get_grant(user_id, app_key):
    """Return the grant row for *user_id* / *app_key*, or ``None``."""
    if not user_id or not valid_app_key(app_key):
        return None
    return UserAppAccess.query.filter_by(user_id=user_id, app_key=app_key).one_or_none()


def app_access_status(user, app_key):
    """Return the grant status for *user*, or ``None`` when no row exists."""
    if user is None:
        return None
    grant = get_grant(user.id, app_key)
    return grant.status if grant else None


def has_app_access(user, app_key):
    """Return whether *user* has effective access to *app_key*."""
    if user is None or not getattr(user, "is_active", False):
        return False
    if is_superadmin(user) and valid_app_key(app_key):
        return True
    return app_access_status(user, app_key) == STATUS_ACTIVE


def app_role(user, app_key):
    """Return *user*'s effective role within *app_key*, if they have access."""
    if user is None:
        return None
    if (
        getattr(user, "is_active", False)
        and is_superadmin(user)
        and valid_app_key(app_key)
    ):
        return "admin"
    grant = get_grant(user.id, app_key)
    if grant is None or grant.status != STATUS_ACTIVE:
        return None
    return grant.role


def _active_grants(user):
    if user is None:
        return []
    return (
        UserAppAccess.query.filter_by(user_id=user.id, status=STATUS_ACTIVE)
        .order_by(UserAppAccess.app_key)
        .all()
    )


def active_app_keys(user):
    """Return the sorted app keys *user* currently has active access to."""
    if user is not None and getattr(user, "is_active", False) and is_superadmin(user):
        return sorted(APP_KEYS)
    return [
        grant.app_key for grant in _active_grants(user) if valid_app_key(grant.app_key)
    ]


def active_app_roles(user):
    """Return ``{app_key: role}`` for every application *user* can access."""
    if user is not None and getattr(user, "is_active", False) and is_superadmin(user):
        return dict.fromkeys(sorted(APP_KEYS), "admin")
    return {
        grant.app_key: grant.role
        for grant in _active_grants(user)
        if valid_app_key(grant.app_key)
    }


def caller_client_id():
    """Return the OAuth client id of the caller, when it is known.

    Only OIDC access tokens carry client identity.  Legacy ``POST /auth``
    tokens do not, so this returns ``None`` for them.
    """
    client = g.get("oidc_client") if g else None
    return client.client_id if client is not None else None


def require_app_access(app_key, role=None):
    """Decorator enforcing an active grant for *app_key* on a Flask route.

    Must be applied **after** an authentication decorator so that
    ``current_user`` is populated.

    Callers presenting a legacy ``POST /auth`` token are always denied: those
    tokens carry no client identity, so there is no way to confirm which
    application is making the request.  Only apply this decorator to routes no
    deployed legacy client calls.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            client = g.get("oidc_client") if g else None
            if client is None:
                logger.warning(
                    "App access check for '%s' denied: caller has no client identity",
                    app_key,
                )
                return jsonify(
                    status=403,
                    detail="This endpoint requires an application access token",
                    error="client_identity_required",
                ), 403
            if client.required_app_key != app_key:
                logger.warning(
                    "App access check for '%s' denied: client %s is registered "
                    "for '%s'",
                    app_key,
                    client.client_id,
                    client.required_app_key,
                )
                return jsonify(
                    status=403,
                    detail="This client is not registered for this application",
                    error="client_app_mismatch",
                    app_key=app_key,
                ), 403
            user = current_user
            if not has_app_access(user, app_key):
                logger.warning(
                    "App access to '%s' denied for user %s (status=%s)",
                    app_key,
                    getattr(user, "id", None),
                    app_access_status(user, app_key),
                )
                return jsonify(
                    status=403,
                    detail=f"Access to {app_label(app_key)} is not activated",
                    error="app_access_required",
                    app_key=app_key,
                    app_access=app_access_status(user, app_key),
                ), 403
            if role is not None and app_role(user, app_key) != role:
                return jsonify(
                    status=403,
                    detail=f"The '{role}' role is required for {app_label(app_key)}",
                    error="app_role_required",
                    app_key=app_key,
                ), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator
