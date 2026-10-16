"""Application-scoped user-token API: ``/api/v1/apps/<app_key>/...``.

One generic implementation serves every application in the registry
(:data:`gefapi.utils.app_access.APP_POLICIES`).  The application key in the
URL is never authority on its own: every request must carry an RS256 OIDC
access token issued to an OAuth client whose ``required_app_key`` matches the
URL, with the required scopes both granted to the token and still registered
for the client.  Account state, the caller's grant, and the caller's
application role are re-read from the database on every request.

Legacy HS256 tokens and service-client tokens carry no OIDC client identity
and are always rejected.  A global ``ADMIN`` is not an application
administrator; effective SUPERADMINs are.
"""

import functools
import logging
import uuid

from flask import g, jsonify, request
from sqlalchemy import func, or_

from gefapi import db
from gefapi.models import User
from gefapi.models.app_access import (
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REVOKED,
    UserAppAccess,
)
from gefapi.routes.api.v1 import endpoints
from gefapi.services.app_access_service import AppAccessError, AppAccessService
from gefapi.utils.app_access import (
    app_access_status,
    app_role,
    get_app_policy,
    has_app_access,
    is_admin_role,
    is_app_admin,
    require_app_access,
)
from gefapi.utils.bearer_auth import bearer_required
from gefapi.utils.oidc_scopes import (
    SCOPE_APP_ACCESS_WRITE,
    SCOPE_APP_READ,
    SCOPE_APP_USERS_READ,
    require_oidc_scopes,
)
from gefapi.utils.permissions import _configured_admin_email, is_superadmin

logger = logging.getLogger(__name__)

BASE = "/apps/<app_key>"
MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 50
MAX_RESOLVE_SUBJECTS = 100
MAX_EMAIL_LENGTH = 254
VALID_STATUSES = (STATUS_PENDING, STATUS_ACTIVE, STATUS_REVOKED)


def _denied(status, error, detail, **extra):
    return jsonify(status=status, error=error, detail=detail, **extra), status


def _verified_email_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        user = g.oidc_user
        if not user.email_verified:
            return _denied(403, "email_unverified", "Email address is not verified")
        return fn(user, *args, **kwargs)

    return wrapper


def app_user_required(*scopes):
    """Authenticate and authorize an application-scoped user request.

    Order: bearer token (401 on any authentication failure, including disabled
    accounts and revoked tokens) -> known application (404) -> OIDC client
    bound to the URL application and current effective grant (403) -> token
    and client scopes (403) -> verified email (403).  The wrapped view receives
    ``(user, policy, app_key, ...)``.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def view(user, app_key, *args, **kwargs):
            return fn(user, get_app_policy(app_key), app_key, *args, **kwargs)

        authorized = require_oidc_scopes(*scopes)(_verified_email_required(view))

        @functools.wraps(fn)
        def wrapper(app_key, *args, **kwargs):
            if get_app_policy(app_key) is None:
                return _denied(404, "unknown_app", "Unknown application")
            return require_app_access(app_key)(authorized)(app_key, *args, **kwargs)

        return bearer_required()(wrapper)

    return decorator


def _admin_denied(policy, app_key):
    return _denied(
        403,
        "app_role_required",
        f"An administering role is required for {policy.label}",
        app_key=app_key,
    )


def _is_eligible(user, app_key):
    """Active, verified account with effective access to *app_key*."""
    return bool(
        user is not None
        and user.is_active
        and user.email_verified
        and has_app_access(user, app_key)
    )


def _superadmin_clause():
    clauses = [User.role == "SUPERADMIN"]
    configured = _configured_admin_email()
    if configured:
        clauses.append(func.lower(User.email) == configured)
    return or_(*clauses)


def _grant_subquery(app_key, status=None):
    query = db.session.query(UserAppAccess.user_id).filter(
        UserAppAccess.app_key == app_key
    )
    if status is not None:
        query = query.filter(UserAppAccess.status == status)
    return query


def serialize_app_profile(user, app_key, fields=None):
    """Return the application-scoped profile for *user*.

    ``app_access`` is ``'active'`` when access is effective (including via
    SUPERADMIN), otherwise the raw grant status or ``None``.  ``app_role`` is
    only reported while access is effective.  *fields* limits the output to the
    application policy's exposed directory fields.
    """
    effective = has_app_access(user, app_key)
    profile = {
        "sub": str(user.id),
        "email": user.email,
        "email_verified": bool(user.email_verified),
        "name": user.name,
        "institution": user.institution,
        "active": bool(user.is_active),
        "app_access": STATUS_ACTIVE if effective else app_access_status(user, app_key),
        "app_role": app_role(user, app_key) if effective else None,
    }
    if fields is None:
        return profile
    return {key: value for key, value in profile.items() if key in fields}


def _directory_profile(user, policy, app_key):
    return serialize_app_profile(user, app_key, policy.directory_fields)


def _parse_uuid(value):
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


@endpoints.route(f"{BASE}/me", strict_slashes=False, methods=["GET"])
@app_user_required(SCOPE_APP_READ)
def app_get_me(user, policy, app_key):
    """Return the caller's profile and effective access to *app_key*."""
    return jsonify(data=serialize_app_profile(user, app_key)), 200


def _lookup_by_email(email, policy, app_key):
    if not isinstance(email, str):
        return _denied(400, "invalid_request", "email must be a string")
    email = email.strip().lower()
    if not email or len(email) > MAX_EMAIL_LENGTH or "@" not in email:
        return _denied(400, "invalid_request", "A valid email address is required")
    matches = User.query.filter(func.lower(User.email) == email).limit(2).all()
    user = matches[0] if len(matches) == 1 else None
    data = (
        [_directory_profile(user, policy, app_key)]
        if _is_eligible(user, app_key)
        else []
    )
    return jsonify(data=data), 200


@endpoints.route(f"{BASE}/users", strict_slashes=False, methods=["GET"])
@app_user_required(SCOPE_APP_USERS_READ)
def app_list_users(user, policy, app_key):
    """Exact-email lookup or the application user directory.

    ``?email=`` returns at most one eligible user (active, verified, effective
    access) and never reveals whether other accounts exist; it requires an
    administering role unless the application allows member discovery.
    Without ``email`` the caller must administer the application; the
    directory lists every user with a grant of any status plus effective
    superadmins.
    """
    admin = is_app_admin(user, app_key)
    if "email" in request.args:
        if not (admin or policy.member_discovery):
            return _admin_denied(policy, app_key)
        return _lookup_by_email(request.args.get("email"), policy, app_key)
    if not admin:
        return _admin_denied(policy, app_key)
    try:
        page = max(int(request.args.get("page", 1)), 1)
        per_page = int(request.args.get("per_page", DEFAULT_PER_PAGE))
    except ValueError:
        return _denied(400, "invalid_request", "page and per_page must be integers")
    if per_page < 1 or per_page > MAX_PER_PAGE:
        return _denied(
            400,
            "invalid_request",
            f"per_page must be between 1 and {MAX_PER_PAGE}",
        )
    status = request.args.get("status")
    if status is not None and status not in VALID_STATUSES:
        return _denied(
            400,
            "invalid_request",
            f"status must be one of: {', '.join(VALID_STATUSES)}",
        )

    superadmins = _superadmin_clause()
    if status is None:
        criteria = or_(User.id.in_(_grant_subquery(app_key)), superadmins)
    elif status == STATUS_ACTIVE:
        criteria = or_(
            User.id.in_(_grant_subquery(app_key, STATUS_ACTIVE)), superadmins
        )
    else:
        criteria = User.id.in_(_grant_subquery(app_key, status)) & ~superadmins
    query = User.query.filter(criteria)
    total = query.count()
    users = (
        query.order_by(func.lower(User.email), User.id)
        .limit(per_page)
        .offset((page - 1) * per_page)
        .all()
    )
    return jsonify(
        data=[_directory_profile(item, policy, app_key) for item in users],
        page=page,
        per_page=per_page,
        total=total,
    ), 200


@endpoints.route(f"{BASE}/users/resolve", strict_slashes=False, methods=["POST"])
@app_user_required(SCOPE_APP_USERS_READ)
def app_resolve_users(user, policy, app_key):
    """Resolve up to 100 subjects to application profiles.

    Members (only where the application allows member discovery) receive only
    eligible users.  Administrators additionally receive users with a pending
    or revoked grant, and inactive accounts holding a grant, matching the
    directory.  Unrelated users are never returned.
    """
    admin = is_app_admin(user, app_key)
    if not (admin or policy.member_discovery):
        return _admin_denied(policy, app_key)
    body = request.get_json(silent=True)
    subjects = body.get("subjects") if isinstance(body, dict) else None
    if not isinstance(subjects, list):
        return _denied(400, "invalid_request", "subjects must be a list")
    if len(subjects) > MAX_RESOLVE_SUBJECTS:
        return _denied(
            400,
            "invalid_request",
            f"At most {MAX_RESOLVE_SUBJECTS} subjects may be resolved per request",
        )
    ordered = []
    for subject in subjects:
        parsed = _parse_uuid(subject)
        if parsed is None:
            return _denied(400, "invalid_request", "subjects must be UUID strings")
        if parsed not in ordered:
            ordered.append(parsed)
    if not ordered:
        return jsonify(data=[]), 200

    users = {item.id: item for item in User.query.filter(User.id.in_(ordered)).all()}
    granted = set()
    if admin:
        granted = {
            row.user_id
            for row in _grant_subquery(app_key)
            .filter(UserAppAccess.user_id.in_(ordered))
            .all()
        }
    data = []
    for subject in ordered:
        target = users.get(subject)
        if target is None:
            continue
        if _is_eligible(target, app_key) or (
            admin and (target.id in granted or is_superadmin(target))
        ):
            data.append(_directory_profile(target, policy, app_key))
    return jsonify(data=data), 200


@endpoints.route(
    f"{BASE}/users/<subject>/access", strict_slashes=False, methods=["PUT"]
)
@app_user_required(SCOPE_APP_ACCESS_WRITE)
def app_set_user_access(user, policy, app_key, subject):
    """Approve, revoke, or change the role of *subject* (administrators only)."""
    if not is_app_admin(user, app_key):
        return _admin_denied(policy, app_key)
    target_id = _parse_uuid(subject)
    if target_id is None:
        return _denied(400, "invalid_request", "subject must be a UUID")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _denied(400, "invalid_request", "A JSON object body is required")
    disallowed = sorted(set(body) - {"status", "role", "note"})
    if disallowed:
        return _denied(
            400,
            "field_not_allowed",
            "Only status, role, and note may be set",
            fields=disallowed,
        )
    status = body.get("status")
    role = body.get("role")
    note = body.get("note")
    if status not in VALID_STATUSES:
        return _denied(
            400,
            "invalid_request",
            f"status must be one of: {', '.join(VALID_STATUSES)}",
        )
    if role is not None and role not in policy.roles:
        return _denied(
            400,
            "invalid_request",
            f"role must be one of: {', '.join(policy.roles)}",
        )
    if note is not None and not isinstance(note, str):
        return _denied(400, "invalid_request", "note must be a string")

    target = db.session.get(User, target_id)
    if target is None:
        return _denied(404, "not_found", "User not found")
    demotes = status != STATUS_ACTIVE or (
        role is not None and not is_admin_role(app_key, role)
    )
    if is_superadmin(target) and demotes:
        return _denied(
            403,
            "superadmin_protected",
            f"Superadmin access to {policy.label} cannot be revoked or demoted",
        )
    if target.id == user.id and demotes:
        return _denied(
            409,
            "self_demotion_forbidden",
            f"You cannot revoke or demote your own {policy.label} access",
        )
    try:
        AppAccessService.set_access(
            user_id=target.id,
            app_key=app_key,
            status=status,
            role=role,
            note=note,
            acting_user=user,
        )
    except AppAccessError as exc:
        payload = {"status": exc.status, "detail": exc.message}
        payload.update(exc.payload)
        return jsonify(payload), exc.status
    db.session.refresh(target)
    return jsonify(data=_directory_profile(target, policy, app_key)), 200
