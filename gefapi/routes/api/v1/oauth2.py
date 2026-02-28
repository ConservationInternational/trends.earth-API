"""OAuth2 routes – Client Credentials grant (RFC 6749 §4.4).

Provides:

* ``POST /oauth/token`` – exchange ``client_id`` + ``client_secret`` for
  a short-lived JWT access token (no prior authentication required).
* ``POST /oauth/clients`` – register a new service client (JWT required).
* ``GET  /oauth/clients`` – list the caller's service clients (JWT required).
* ``DELETE /oauth/clients/<id>`` – revoke a service client (JWT required).
"""

from datetime import timedelta
import logging
import os

from flask import jsonify, request
from flask_jwt_extended import (
    create_access_token,
    current_user,
    jwt_required,
)

from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.oauth2_service import OAuth2Service

logger = logging.getLogger(__name__)

# Access tokens issued via client_credentials last 30 minutes by default.
# Callers should request a new token when the current one expires.
OAUTH2_TOKEN_LIFETIME_SECONDS = int(os.getenv("OAUTH2_TOKEN_LIFETIME_SECONDS", "1800"))


# -------------------------------------------------------------------------
# Token endpoint (public – no JWT required)
# -------------------------------------------------------------------------


@endpoints.route("/oauth/token", strict_slashes=False, methods=["POST"])
def oauth2_token():
    """Exchange client credentials for a short-lived access token.

    Accepts either ``application/x-www-form-urlencoded`` (standard) or
    ``application/json`` request bodies.

    Required parameters:

    * ``grant_type`` – must be ``"client_credentials"``
    * ``client_id``
    * ``client_secret``
    """
    if request.content_type and "json" in request.content_type:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form

    grant_type = data.get("grant_type")
    if grant_type != "client_credentials":
        return (
            jsonify(
                {
                    "error": "unsupported_grant_type",
                    "error_description": (
                        "Only grant_type=client_credentials is supported"
                    ),
                }
            ),
            400,
        )

    client_id = data.get("client_id")
    client_secret = data.get("client_secret")

    if not client_id or not client_secret:
        return (
            jsonify(
                {
                    "error": "invalid_request",
                    "error_description": ("client_id and client_secret are required"),
                }
            ),
            400,
        )

    try:
        user = OAuth2Service.authenticate(client_id, client_secret)
    except Exception:
        logger.debug("OAuth2 client_credentials auth failed for %s", client_id)
        return (
            jsonify(
                {
                    "error": "invalid_client",
                    "error_description": "Invalid client credentials",
                }
            ),
            401,
        )

    expires = timedelta(seconds=OAUTH2_TOKEN_LIFETIME_SECONDS)
    access_token = create_access_token(
        identity=user.id,
        expires_delta=expires,
        additional_claims={"grant_type": "client_credentials"},
    )

    return (
        jsonify(
            {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": OAUTH2_TOKEN_LIFETIME_SECONDS,
            }
        ),
        200,
    )


# -------------------------------------------------------------------------
# Client management endpoints (JWT required)
# -------------------------------------------------------------------------


@endpoints.route("/oauth/clients", strict_slashes=False, methods=["POST"])
@jwt_required()
def create_oauth2_client():
    """Register a new OAuth2 service client.

    Request body (JSON):

    * ``name`` (str, required) – human-readable label.
    * ``scopes`` (str, optional) – space-delimited scope list.
    * ``expires_in_days`` (int, optional) – lifetime in days.

    Response (201): client metadata **including the one-time
    ``client_secret``**.
    """
    body = request.get_json(silent=True) or {}

    name = body.get("name")
    if not name:
        return error(400, "Missing required field: name")

    scopes = body.get("scopes", "")
    expires_in_days = body.get("expires_in_days")
    if expires_in_days is not None:
        try:
            expires_in_days = int(expires_in_days)
            if expires_in_days < 1:
                raise ValueError
        except (TypeError, ValueError):
            return error(400, "expires_in_days must be a positive integer")

    try:
        raw_secret, client = OAuth2Service.create_client(
            user=current_user,
            name=name,
            scopes=scopes,
            expires_in_days=expires_in_days,
        )
    except Exception as exc:
        logger.warning("OAuth2 client creation failed: %s", exc)
        return error(400, str(exc))

    data = client.serialize()
    data["client_secret"] = raw_secret  # One-time disclosure
    return jsonify({"data": data}), 201


@endpoints.route("/oauth/clients", strict_slashes=False, methods=["GET"])
@jwt_required()
def list_oauth2_clients():
    """List the caller's active (non-revoked) service clients.

    The ``client_secret`` is **never** returned.
    """
    clients = OAuth2Service.list_clients(current_user)
    return jsonify({"data": [c.serialize() for c in clients]}), 200


@endpoints.route(
    "/oauth/clients/<client_db_id>", strict_slashes=False, methods=["DELETE"]
)
@jwt_required()
def revoke_oauth2_client(client_db_id):
    """Revoke a service client by its database UUID.

    Only the owner or an admin may revoke.
    """
    try:
        client = OAuth2Service.revoke_client(client_db_id, current_user)
        return jsonify({"data": client.serialize()}), 200
    except Exception as exc:
        logger.warning("OAuth2 client revocation failed: %s", exc)
        return error(400, str(exc))
