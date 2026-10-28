"""Per-application access control for first-party Trends.Earth applications.

The API is the single identity provider for several first-party applications.
Some of them are *gated*: a user may only sign in once an administrator has
activated their access.  Gating is declared per OAuth client via
``OAuthClient.required_app_key``, and each user's access is recorded in the
``user_app_access`` table.

Absence of a grant row means **no access**.  Nothing here ever default-allows.
"""

from dataclasses import dataclass
import functools
import logging
import os
import urllib.parse

from flask import g, jsonify
from flask_jwt_extended import current_user

from gefapi.models.app_access import STATUS_ACTIVE, UserAppAccess
from gefapi.utils.permissions import is_superadmin

logger = logging.getLogger(__name__)

APP_AVOIDED_EMISSIONS = "avoided_emissions"
# Staging/local Avoided Emissions deployments: production accounts, separate grants.
APP_AVOIDED_EMISSIONS_STAGING = "avoided_emissions_staging"
APP_RIO_COHERENCE = "rio_coherence"
# Staging Rio deployments sign in with production accounts but need their own grants.
APP_RIO_COHERENCE_STAGING = "rio_coherence_staging"

# Fields an application-scoped profile response may contain.
PROFILE_RESPONSE_FIELDS = (
    "sub",
    "email",
    "email_verified",
    "name",
    "institution",
    "active",
    "app_access",
    "app_role",
)


@dataclass(frozen=True)
class AppPolicy:
    """Explicit per-application vocabulary and capability policy.

    Every capability must be declared; anything not declared is denied.
    Construction fails when the policy is internally inconsistent so that a
    misconfiguration cannot silently widen access.
    """

    label: str
    roles: tuple
    default_role: str
    admin_roles: frozenset
    superadmin_role: str
    member_discovery: bool
    directory_fields: tuple

    def __post_init__(self):
        if not self.roles or len(set(self.roles)) != len(self.roles):
            raise ValueError("roles must be a non-empty tuple of unique names")
        if self.default_role not in self.roles:
            raise ValueError("default_role must be one of roles")
        if not self.admin_roles or not set(self.admin_roles) <= set(self.roles):
            raise ValueError("admin_roles must be a non-empty subset of roles")
        if self.default_role in self.admin_roles:
            raise ValueError("default_role must not be an administering role")
        if self.superadmin_role not in self.admin_roles:
            raise ValueError("superadmin_role must be an administering role")
        if not isinstance(self.member_discovery, bool):
            raise TypeError("member_discovery must be explicitly True or False")
        fields = set(self.directory_fields)
        if "sub" not in fields or not fields <= set(PROFILE_RESPONSE_FIELDS):
            raise ValueError("directory_fields must include sub and known fields")


# The application registry.  ``member_discovery`` controls whether ordinary
# members may look up other eligible users (exact email / subject resolution);
# application administrators always can.
APP_POLICIES = {
    APP_AVOIDED_EMISSIONS: AppPolicy(
        label="Avoided Emissions",
        roles=("member", "admin"),
        default_role="member",
        admin_roles=frozenset({"admin"}),
        superadmin_role="admin",
        member_discovery=False,
        directory_fields=PROFILE_RESPONSE_FIELDS,
    ),
    APP_AVOIDED_EMISSIONS_STAGING: AppPolicy(
        label="Avoided Emissions (staging)",
        roles=("member", "admin"),
        default_role="member",
        admin_roles=frozenset({"admin"}),
        superadmin_role="admin",
        member_discovery=False,
        directory_fields=PROFILE_RESPONSE_FIELDS,
    ),
    APP_RIO_COHERENCE: AppPolicy(
        label="Rio Coherence",
        roles=("member", "admin"),
        default_role="member",
        admin_roles=frozenset({"admin"}),
        superadmin_role="admin",
        # Group owners add eligible members before their first Rio sign-in.
        member_discovery=True,
        directory_fields=PROFILE_RESPONSE_FIELDS,
    ),
    APP_RIO_COHERENCE_STAGING: AppPolicy(
        label="Rio Coherence (staging)",
        roles=("member", "admin"),
        default_role="member",
        admin_roles=frozenset({"admin"}),
        superadmin_role="admin",
        member_discovery=True,
        directory_fields=PROFILE_RESPONSE_FIELDS,
    ),
}

APP_KEYS = frozenset(APP_POLICIES)
APP_LABELS = {key: policy.label for key, policy in APP_POLICIES.items()}
# Per-application role vocabularies.  These replace each application's own
# local role model so that no application needs a local user store.
APP_ROLES = {key: policy.roles for key, policy in APP_POLICIES.items()}


def get_app_policy(app_key):
    """Return the :class:`AppPolicy` for *app_key*, or ``None`` when unknown."""
    if not isinstance(app_key, str):
        return None
    return APP_POLICIES.get(app_key)


def valid_app_key(app_key):
    """Return True when *app_key* names a known gated application."""
    return get_app_policy(app_key) is not None


def app_label(app_key):
    """Return the human-readable label for *app_key*."""
    policy = get_app_policy(app_key)
    return policy.label if policy else app_key


def app_roles_for(app_key):
    """Return *app_key*'s role vocabulary (empty for unknown applications)."""
    policy = get_app_policy(app_key)
    return policy.roles if policy else ()


def valid_app_role(app_key, role):
    """Return True when *role* is part of *app_key*'s role vocabulary."""
    return role in app_roles_for(app_key)


def default_role(app_key):
    """Return the default role granted for *app_key* (``None`` if unknown)."""
    policy = get_app_policy(app_key)
    return policy.default_role if policy else None


def is_admin_role(app_key, role):
    """Return whether *role* administers *app_key*."""
    policy = get_app_policy(app_key)
    return bool(policy and role in policy.admin_roles)


def app_admin_url(app_key):
    """Return the trusted admin-page URL where *app_key* admins approve access.

    Read from deployment configuration (``<APP_KEY>_ADMIN_URL``, e.g.
    ``AVOIDED_EMISSIONS_STAGING_ADMIN_URL``), never from request data.  Only
    ``https`` URLs (or ``http`` on a loopback host, for local development) are
    accepted; anything else returns ``None`` so that no link is sent.
    """
    if not valid_app_key(app_key):
        return None
    raw = (os.getenv(f"{app_key.upper()}_ADMIN_URL") or "").strip()
    if not raw:
        return None
    parsed = urllib.parse.urlsplit(raw)
    if not parsed.netloc or parsed.username or parsed.password:
        return None
    if parsed.scheme == "https" or (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    ):
        return raw
    logger.warning("Ignoring invalid %s_ADMIN_URL", app_key.upper())
    return None


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
    policy = get_app_policy(app_key)
    if policy is None:
        return None
    if getattr(user, "is_active", False) and is_superadmin(user):
        return policy.superadmin_role
    grant = get_grant(user.id, app_key)
    if grant is None or grant.status != STATUS_ACTIVE:
        return None
    return grant.role


def is_app_admin(user, app_key):
    """Return whether *user* currently administers *app_key*.

    Reads the live grant (or effective superadmin status) on every call; a
    global ``ADMIN`` role does not make a user an application administrator.
    """
    return has_app_access(user, app_key) and is_admin_role(
        app_key, app_role(user, app_key)
    )


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
        return {key: APP_POLICIES[key].superadmin_role for key in sorted(APP_KEYS)}
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
