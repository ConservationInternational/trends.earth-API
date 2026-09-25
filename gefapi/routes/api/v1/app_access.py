"""Per-application access routes for the Trends.Earth API."""

import logging

from flask import jsonify, request
from flask_jwt_extended import current_user, jwt_required

from gefapi.models.app_access import STATUS_ACTIVE, STATUS_PENDING, STATUS_REVOKED
from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.app_access_service import AppAccessError, AppAccessService
from gefapi.utils.app_access import (
    APP_KEYS,
    APP_LABELS,
    APP_ROLES,
    valid_app_key,
)
from gefapi.utils.permissions import is_admin_or_higher
from gefapi.utils.scopes import require_scope

logger = logging.getLogger(__name__)

VALID_STATUSES = (STATUS_PENDING, STATUS_ACTIVE, STATUS_REVOKED)


def _app_access_error(exc):
    payload = {"status": exc.status, "detail": exc.message}
    payload.update(exc.payload)
    return jsonify(payload), exc.status


@endpoints.route("/admin/app-access", strict_slashes=False, methods=["GET"])
@jwt_required()
@require_scope("admin:read")
def list_app_access():
    """List per-application access grants.

    **Authentication**: JWT token required
    **Access**: ADMIN or SUPERADMIN

    **Query Parameters**:
    - `app`: Filter by application key (`avoided_emissions`, `rio_coherence`)
    - `status`: Filter by grant status (`pending`, `active`, `revoked`)
    - `role`: Filter by application role
    - `user_id`: Filter by user
    - `page` / `per_page`: Pagination (default 1 / 100, max 1000)
    - `include`: Comma-separated extras; `user` embeds requester details
    """
    if not is_admin_or_higher(current_user):
        return error(status=403, detail="Forbidden")
    try:
        page = max(int(request.args.get("page", 1)), 1)
        per_page = min(max(int(request.args.get("per_page", 100)), 1), 1000)
    except ValueError:
        return error(status=400, detail="page and per_page must be integers")

    include = [item for item in request.args.get("include", "").split(",") if item]
    try:
        query = AppAccessService.list_grants(
            app_key=request.args.get("app"),
            status=request.args.get("status"),
            role=request.args.get("role"),
            user_id=request.args.get("user_id"),
        )
    except AppAccessError as exc:
        return _app_access_error(exc)

    total = query.count()
    grants = query.limit(per_page).offset((page - 1) * per_page).all()
    data = []
    for grant in grants:
        item = grant.serialize()
        if "user" in include and grant.user is not None:
            item["user"] = {
                "id": str(grant.user.id),
                "email": grant.user.email,
                "name": grant.user.name,
                "institution": grant.user.institution,
                "country": grant.user.country,
            }
        data.append(item)
    return jsonify(data=data, page=page, per_page=per_page, total=total), 200


@endpoints.route("/admin/app-access/apps", strict_slashes=False, methods=["GET"])
@jwt_required()
@require_scope("admin:read")
def list_gated_apps():
    """List the gated applications and their role vocabularies."""
    if not is_admin_or_higher(current_user):
        return error(status=403, detail="Forbidden")
    return jsonify(
        data=[
            {
                "app_key": app_key,
                "label": APP_LABELS[app_key],
                "roles": list(APP_ROLES[app_key]),
            }
            for app_key in sorted(APP_KEYS)
        ]
    ), 200


@endpoints.route(
    "/admin/users/<user_id>/app-access", strict_slashes=False, methods=["POST"]
)
@jwt_required()
@require_scope("admin:write")
def set_user_app_access(user_id):
    """Grant, update, or revoke a user's access to a gated application.

    **Authentication**: JWT token required
    **Access**: ADMIN or SUPERADMIN

    **Request Schema**:
    ```json
    {
      "app_key": "avoided_emissions",
      "status": "active",
      "role": "member",
      "note": "Approved for the 2026 pilot"
    }
    ```
    """
    if not is_admin_or_higher(current_user):
        return error(status=403, detail="Forbidden")
    body = request.get_json(silent=True) or {}
    app_key = body.get("app_key")
    status = body.get("status", STATUS_ACTIVE)
    if not app_key:
        return error(status=400, detail="app_key is required")
    if status not in VALID_STATUSES:
        return error(
            status=400,
            detail=f"status must be one of: {', '.join(VALID_STATUSES)}",
        )
    try:
        grant = AppAccessService.set_access(
            user_id=user_id,
            app_key=app_key,
            status=status,
            role=body.get("role"),
            note=body.get("note"),
            acting_user=current_user,
        )
    except AppAccessError as exc:
        return _app_access_error(exc)
    return jsonify(data=grant.serialize()), 200


@endpoints.route(
    "/admin/users/<user_id>/app-access/<app_key>",
    strict_slashes=False,
    methods=["DELETE"],
)
@jwt_required()
@require_scope("admin:write")
def revoke_user_app_access(user_id, app_key):
    """Revoke a user's access to a gated application.

    The grant row is retained with ``status='revoked'`` so the decision stays
    auditable, and the user cannot silently re-request access.
    """
    if not is_admin_or_higher(current_user):
        return error(status=403, detail="Forbidden")
    body = request.get_json(silent=True) or {}
    try:
        grant = AppAccessService.set_access(
            user_id=user_id,
            app_key=app_key,
            status=STATUS_REVOKED,
            note=body.get("note"),
            acting_user=current_user,
        )
    except AppAccessError as exc:
        return _app_access_error(exc)
    return jsonify(data=grant.serialize()), 200


@endpoints.route("/user/me/app-access", strict_slashes=False, methods=["GET"])
@jwt_required()
@require_scope("user:read")
def get_my_app_access():
    """List the caller's own application grants and pending requests."""
    grants = {
        grant.app_key: grant
        for grant in AppAccessService.list_for_user(current_user.id)
    }
    data = []
    for app_key in sorted(APP_KEYS):
        grant = grants.get(app_key)
        data.append(
            {
                "app_key": app_key,
                "label": APP_LABELS[app_key],
                "status": grant.status if grant else None,
                "role": grant.role if grant and grant.status == STATUS_ACTIVE else None,
                "requested_at": grant.requested_at.isoformat()
                if grant and grant.requested_at
                else None,
                "granted_at": grant.granted_at.isoformat()
                if grant and grant.granted_at
                else None,
            }
        )
    return jsonify(data=data), 200


@endpoints.route(
    "/user/me/app-access/<app_key>", strict_slashes=False, methods=["POST"]
)
@jwt_required()
@require_scope("user:write")
def request_my_app_access(app_key):
    """Request access to a gated application.

    Idempotent: re-requesting while a request is already pending updates the
    note without creating a duplicate or re-notifying administrators.  Access
    that an administrator has revoked cannot be re-requested here.
    """
    if not valid_app_key(app_key):
        return error(status=404, detail=f"Unknown application '{app_key}'")
    body = request.get_json(silent=True) or {}
    try:
        grant, created = AppAccessService.request_access(
            user=current_user,
            app_key=app_key,
            request_note=body.get("request_note"),
        )
    except AppAccessError as exc:
        return _app_access_error(exc)
    return jsonify(data=grant.serialize(), created=created), 201 if created else 200
