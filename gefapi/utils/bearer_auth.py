"""Bearer token verification supporting both legacy HS256 and OIDC RS256 tokens.

The API historically issues HS256 tokens from ``POST /auth`` via
flask-jwt-extended.  OIDC clients present RS256 access tokens issued by the
provider in :mod:`gefapi.services.oidc_service`.

Verification is strictly ordered: HS256 first, RS256 only as a fallback.  A
legacy caller therefore never reaches the OIDC path and its behaviour is
unchanged.

**When both paths fail the original flask-jwt-extended exception is re-raised**
so that the ``expired_token_loader`` / ``invalid_token_loader`` /
``unauthorized_loader`` / ``revoked_token_loader`` callbacks still produce the
exact 401 status and JSON bodies that deployed clients depend on.
"""

import functools
import logging

from authlib.jose.errors import JoseError
from flask import g
from flask_jwt_extended import verify_jwt_in_request

logger = logging.getLogger(__name__)


def _verify_oidc_access_token():
    """Verify an RS256 OIDC access token from the Authorization header.

    Returns the resolved ``(user, claims, client)`` triple, or ``None`` when the
    request does not carry a valid OIDC access token.
    """
    from flask import request

    from gefapi import db, is_token_in_blocklist
    from gefapi.models import OAuthClient, User
    from gefapi.services.oidc_service import decode_token

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    raw_token = header.removeprefix("Bearer ").strip()
    if not raw_token:
        return None

    try:
        claims = decode_token(raw_token, "access_token")
    except (JoseError, ValueError, TypeError, KeyError):
        return None

    client = None
    client_id = claims.get("azp") or claims.get("client_id")
    if client_id:
        client = OAuthClient.query.filter_by(
            client_id=client_id, is_active=True
        ).first()
    if client is None and claims.get("aud"):
        # Tokens issued before client identity claims were added.
        client = OAuthClient.query.filter_by(
            audience=claims["aud"], is_active=True
        ).first()
    if client is None:
        return None

    try:
        claims = decode_token(raw_token, "access_token", audience=client.audience)
    except (JoseError, ValueError, TypeError, KeyError):
        return None

    if is_token_in_blocklist(claims.get("jti")):
        return None

    user = db.session.get(User, claims.get("sub"))
    if user is None or not user.is_active:
        return None
    return user, claims, client


def verify_bearer_token(optional=False):
    """Verify the request's bearer token, accepting HS256 or RS256.

    Mirrors ``verify_jwt_in_request`` semantics.  On failure of both paths the
    original flask-jwt-extended exception propagates untouched.
    """
    try:
        verify_jwt_in_request(optional=optional)
    except Exception:
        resolved = _verify_oidc_access_token()
        if resolved is None:
            # Preserve the legacy 401 status and error body exactly.
            raise
        user, claims, client = resolved
        g.oidc_user = user
        g.oidc_claims = claims
        g.oidc_client = client
        g._jwt_extended_jwt = claims
        g._jwt_extended_jwt_header = {"alg": "RS256"}
        g._jwt_extended_jwt_user = {"loaded_user": user}
        g._jwt_extended_jwt_location = "headers"
        logger.debug("Authenticated OIDC access token for client %s", client.client_id)


def bearer_required(optional=False):
    """Decorator accepting either a legacy HS256 token or an OIDC RS256 token.

    Drop-in replacement for ``@jwt_required()`` on routes that should be
    reachable by OIDC clients.  Existing routes keep using ``@jwt_required()``
    and are unaffected.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            verify_bearer_token(optional=optional)
            return fn(*args, **kwargs)

        return wrapper

    return decorator
