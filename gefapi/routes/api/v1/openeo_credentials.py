"""API routes for openEO credential management."""

import logging

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from gefapi import db
from gefapi.models.user import User
from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.openeo_credential_service import OpenEOCredentialService
from gefapi.utils.scopes import require_scope

logger = logging.getLogger(__name__)


@endpoints.route("/user/me/openeo-credentials", strict_slashes=False, methods=["GET"])
@jwt_required()
@require_scope("gee:read")
def get_user_openeo_credentials():
    """Get the current user's openEO credential status (no secrets returned).

    **Authentication**: JWT token required.

    **Response Schema**::

        {
          "data": {
            "has_credentials": true,
            "credential_type": "oidc_refresh_token"
          }
        }

    **Fields**:
    - ``has_credentials``: whether any credentials are stored.
    - ``credential_type``: the ``type`` key inside the stored credentials dict,
      or ``null`` when none are stored.

    **Error Responses**:
    - ``401 Unauthorized``
    - ``404 Not Found``: user not found
    - ``500 Internal Server Error``
    """
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        if not user:
            return error(status=404, detail="User not found")

        cred_type = None
        if user.has_openeo_credentials():
            creds = user.get_openeo_credentials() or {}
            cred_type = creds.get("type")

        return jsonify(
            {
                "data": {
                    "has_credentials": user.has_openeo_credentials(),
                    "credential_type": cred_type,
                }
            }
        )
    except Exception as exc:
        logger.error("Error fetching openEO credentials status: %s", exc)
        return error(status=500, detail="Internal server error")


@endpoints.route("/user/me/openeo-credentials", strict_slashes=False, methods=["POST"])
@jwt_required()
@require_scope("gee:write")
def set_user_openeo_credentials():
    """Store (or replace) openEO credentials for the current user.

    **Authentication**: JWT token required.

    **Request Body** (JSON):

    *OIDC refresh-token credentials*::

        {
          "type": "oidc_refresh_token",
          "provider_id": "egi",
          "client_id": "trends-earth",
          "client_secret": "...",
          "refresh_token": "..."
        }

    *Basic-auth credentials*::

        {
          "type": "basic",
          "username": "user@example.com",
          "password": "..."
        }

    **Response**: ``200 OK`` with ``{"data": {"status": "credentials stored"}}``

    **Error Responses**:
    - ``400 Bad Request``: missing / invalid payload
    - ``401 Unauthorized``
    - ``404 Not Found``: user not found
    - ``500 Internal Server Error``
    """
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        if not user:
            return error(status=404, detail="User not found")

        payload = request.get_json(silent=True)
        if not payload:
            return error(status=400, detail="JSON body required")

        cred_type = payload.get("type")
        if cred_type not in ("oidc_refresh_token", "basic"):
            return error(
                status=400,
                detail=(
                    "Invalid credential type.  Must be 'oidc_refresh_token' or 'basic'."
                ),
            )

        if cred_type == "oidc_refresh_token":
            required = ("client_id", "refresh_token")
        else:
            required = ("username", "password")

        missing = [k for k in required if not payload.get(k)]
        if missing:
            return error(
                status=400, detail=f"Missing required fields: {', '.join(missing)}"
            )

        # Whitelist: only store fields that are expected for each credential type.
        # This prevents arbitrary client-supplied keys from being persisted and
        # later injected into container environment variables.
        if cred_type == "oidc_refresh_token":
            allowed_keys = {
                "type",
                "client_id",
                "client_secret",
                "refresh_token",
                "provider_id",
            }
        else:  # basic
            allowed_keys = {"type", "username", "password"}
        filtered = {k: v for k, v in payload.items() if k in allowed_keys}

        user.set_openeo_credentials(filtered)
        db.session.commit()

        return jsonify({"data": {"status": "credentials stored"}})
    except Exception as exc:
        logger.error("Error storing openEO credentials: %s", exc)
        return error(status=500, detail="Internal server error")


@endpoints.route(
    "/user/me/openeo-credentials", strict_slashes=False, methods=["DELETE"]
)
@jwt_required()
@require_scope("gee:write")
def delete_user_openeo_credentials():
    """Remove the current user's stored openEO credentials.

    **Authentication**: JWT token required.

    **Response**: ``200 OK`` with ``{"data": {"status": "credentials removed"}}``

    **Error Responses**:
    - ``401 Unauthorized``
    - ``404 Not Found``: user not found
    - ``500 Internal Server Error``
    """
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        if not user:
            return error(status=404, detail="User not found")

        user.clear_openeo_credentials()
        db.session.commit()

        return jsonify({"data": {"status": "credentials removed"}})
    except Exception as exc:
        logger.error("Error removing openEO credentials: %s", exc)
        return error(status=500, detail="Internal server error")


@endpoints.route(
    "/user/me/openeo-credentials/check", strict_slashes=False, methods=["GET"]
)
@jwt_required()
@require_scope("gee:read")
def check_user_openeo_credentials():
    """Validate the current user's stored openEO credentials against the backend.

    Makes a live request to the configured openEO backend.

    **Authentication**: JWT token required.

    **Response Schema**::

        {
          "data": {
            "valid": true,
            "message": "Credentials are valid"
          }
        }

    **Error Responses**:
    - ``401 Unauthorized``
    - ``404 Not Found``: user not found
    - ``500 Internal Server Error``
    """
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        if not user:
            return error(status=404, detail="User not found")

        if not user.has_openeo_credentials():
            return jsonify(
                {
                    "data": {
                        "valid": False,
                        "message": "No credentials stored",
                    }
                }
            )

        valid = OpenEOCredentialService.validate_credentials(user)
        message = (
            "Credentials are valid"
            if valid
            else "Credentials are invalid or backend unreachable"
        )
        return jsonify({"data": {"valid": valid, "message": message}})
    except Exception as exc:
        logger.error("Error checking openEO credentials: %s", exc)
        return error(status=500, detail="Internal server error")
