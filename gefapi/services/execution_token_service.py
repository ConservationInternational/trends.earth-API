"""Execution-bound callback credentials, separate from user authentication."""

import datetime
import hashlib
import hmac
import logging
import uuid

from flask import current_app
import jwt

from gefapi.config import SETTINGS
from gefapi.models import Execution, User

logger = logging.getLogger(__name__)
_AUDIENCE = "trends-earth-execution-callback"
_ISSUER = "trends-earth-api"
_ACTIVE_STATES = {"PENDING", "READY", "RUNNING", "CANCELLING"}


def _signing_key():
    explicit = SETTINGS.get("EXECUTION_TOKEN_SECRET")
    if explicit:
        return explicit
    # Domain separation prevents callback tokens being accepted as user JWTs.
    secret = current_app.config["JWT_SECRET_KEY"]
    return hmac.new(
        secret.encode(), b"trends-earth:execution-callback:v1", hashlib.sha256
    ).digest()


def create_execution_token(execution_id, user):
    now = datetime.datetime.now(datetime.UTC)
    return jwt.encode(
        {
            "sub": str(execution_id),
            "owner": str(user.id),
            "auth_version": user.auth_version,
            "purpose": "execution_callback",
            "aud": _AUDIENCE,
            "iss": _ISSUER,
            "iat": now,
            "exp": now
            + datetime.timedelta(seconds=SETTINGS["EXECUTION_TOKEN_TTL_SECONDS"]),
            "jti": str(uuid.uuid4()),
        },
        _signing_key(),
        algorithm="HS256",
    )


def verify_execution_token(token, execution_id):
    """Raise InvalidTokenError unless the token authorizes this active execution."""
    claims = jwt.decode(
        token,
        _signing_key(),
        algorithms=["HS256"],
        audience=_AUDIENCE,
        issuer=_ISSUER,
        options={
            "require": [
                "sub",
                "owner",
                "auth_version",
                "purpose",
                "aud",
                "iss",
                "iat",
                "exp",
                "jti",
            ]
        },
    )
    if claims["purpose"] != "execution_callback" or claims["sub"] != execution_id:
        raise jwt.InvalidTokenError("Callback token does not match this execution")
    execution = Execution.query.filter_by(id=execution_id).first()
    if (
        execution is None
        or str(execution.user_id) != claims["owner"]
        or execution.status not in _ACTIVE_STATES
    ):
        raise jwt.InvalidTokenError("Callback execution is not active")
    user = User.query.filter_by(id=execution.user_id).first()
    if (
        user is None
        or not user.is_active
        or user.auth_version != claims["auth_version"]
    ):
        raise jwt.InvalidTokenError("Callback owner credentials have been revoked")
    return execution
