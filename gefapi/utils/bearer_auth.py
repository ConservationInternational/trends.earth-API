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

import base64
import functools
import json
import logging

from authlib.jose.errors import JoseError
from flask import g, request
from flask_jwt_extended import get_current_user, verify_jwt_in_request

logger = logging.getLogger(__name__)

_RATE_LIMIT_USER_UNSET = object()
_RATE_LIMIT_USER_KEY = "gefapi.rate_limit_user"


def _raw_bearer_token():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header.removeprefix("Bearer ").strip() or None


def _is_rs256_candidate(raw_token):
    """Return whether *raw_token*'s (unverified) JOSE header declares RS256.

    The unverified header only selects which verifier to try; it never
    authorizes anything.  Legacy HS256 tokens (and anything malformed) are
    never handed to the OIDC verifier, so their failures keep their original
    flask-jwt-extended error and never depend on OIDC keys or clients.
    """
    if not raw_token or raw_token.count(".") != 2:
        return False
    segment = raw_token.split(".", 1)[0]
    try:
        padded = segment + "=" * (-len(segment) % 4)
        header = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return False
    return isinstance(header, dict) and header.get("alg") == "RS256"


def _verify_oidc_access_token():
    """Verify an RS256 OIDC access token from the Authorization header.

    Returns the resolved ``(user, claims, client)`` triple, or ``None`` when the
    request does not carry a valid OIDC access token.
    """
    from gefapi import db, is_token_in_blocklist
    from gefapi.models import User
    from gefapi.services.oidc_service import access_token_client, decode_token

    raw_token = _raw_bearer_token()
    if not _is_rs256_candidate(raw_token):
        return None

    try:
        claims = decode_token(raw_token, "access_token")
    except (JoseError, ValueError, TypeError, KeyError):
        return None

    if (
        claims.get("token_use") != "access_token"
        or not isinstance(claims.get("jti"), str)
        or not claims["jti"]
        or claims.get("purpose") not in (None, "access")
    ):
        return None

    client = access_token_client(claims)
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
    # Password changes and forced sign-outs bump auth_version; tokens issued
    # before that stop working (tokens predating the claim expire within 5 min).
    if "auth_version" in claims and claims["auth_version"] != user.auth_version:
        return None
    return user, claims, client


def verify_bearer_token(optional=False):
    """Verify the request's bearer token, accepting HS256 or RS256.

    Mirrors ``verify_jwt_in_request`` semantics.  On failure of both paths the
    original flask-jwt-extended exception propagates untouched.
    """
    # Never let OIDC context from an earlier request in a shared app context
    # (tests, CLI) leak into a legacy-authenticated request.
    for slot in ("oidc_user", "oidc_claims", "oidc_client"):
        g.pop(slot, None)
    try:
        verify_jwt_in_request(optional=optional)
    except Exception:
        if not _is_rs256_candidate(_raw_bearer_token()):
            raise
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


def resolve_request_user():
    """Return the authenticated user for rate-limit keys, or ``None``.

    Legacy HS256 verification runs first, exactly as the existing key
    functions do.  Only when that fails for an RS256 token is the OIDC token
    verified.  The result is cached per request and the OIDC authentication
    slots are **not** populated, so a key function can never make a request
    look authenticated to the route itself.
    """
    cached = request.environ.get(_RATE_LIMIT_USER_KEY, _RATE_LIMIT_USER_UNSET)
    if cached is not _RATE_LIMIT_USER_UNSET:
        return cached
    user = None
    try:
        verify_jwt_in_request(optional=True)
        user = get_current_user()
    except Exception as exc:  # rate-limit keys never raise
        logger.debug("Legacy token not usable for rate limiting: %s", exc)
        try:
            resolved = _verify_oidc_access_token()
        except Exception as oidc_exc:
            logger.debug("OIDC token not usable for rate limiting: %s", oidc_exc)
            resolved = None
        if resolved is not None:
            user = resolved[0]
    request.environ[_RATE_LIMIT_USER_KEY] = user
    return user


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
