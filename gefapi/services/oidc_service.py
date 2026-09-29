"""Authlib-backed signing and validation helpers for the Trends.Earth OIDC contract."""

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import os
import re
import secrets
from urllib.parse import urlsplit
import uuid

from authlib.jose import JsonWebKey, jwt
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from werkzeug.security import generate_password_hash

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import AuthorizationCode, OAuthClient, OIDCRefreshToken

_PRIVATE_KEYS = None
PKCE_CODE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
PKCE_CODE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
TRENDS_API_AUDIENCE = "https://api.trends.earth"


def issuer():
    value = SETTINGS.get("OIDC_ISSUER") or os.getenv("API_PUBLIC_URL")
    return (value or "http://localhost:3000").rstrip("/")


def key_id():
    return SETTINGS.get("OIDC_KEY_ID", "trends-api-1")


def _private_keys():
    global _PRIVATE_KEYS
    if _PRIVATE_KEYS is None:
        configured_keys = SETTINGS.get("OIDC_PRIVATE_KEYS") or os.getenv(
            "OIDC_PRIVATE_KEYS"
        )
        _PRIVATE_KEYS = {}
        if configured_keys:
            values = json.loads(configured_keys)
            _PRIVATE_KEYS.update(
                {
                    kid: JsonWebKey.import_key(
                        value.replace("\\n", "\n"), {"kty": "RSA"}
                    )
                    for kid, value in values.items()
                }
            )
        configured = SETTINGS.get("OIDC_PRIVATE_KEY") or os.getenv("OIDC_PRIVATE_KEY")
        if configured:
            _PRIVATE_KEYS[key_id()] = JsonWebKey.import_key(
                configured.replace("\\n", "\n"), {"kty": "RSA"}
            )
        if not _PRIVATE_KEYS:
            if os.getenv("ENVIRONMENT") in ("prod", "production", "staging"):
                raise RuntimeError(
                    "OIDC_PRIVATE_KEY or OIDC_PRIVATE_KEYS must be configured "
                    "in production"
                )
            _PRIVATE_KEYS[key_id()] = JsonWebKey.generate_key(
                "RSA", 2048, is_private=True
            )
    return _PRIVATE_KEYS


def _jwks():
    return {
        "keys": [
            {
                **key.as_dict(is_private=False),
                "kid": kid,
                "use": "sig",
                "alg": "RS256",
            }
            for kid, key in _private_keys().items()
        ]
    }


def jwks():
    """Return public signing keys, including overlapping rotation keys."""
    return _jwks()


def valid_pkce_code_challenge(challenge):
    """Validate a S256 PKCE code challenge per RFC 7636 syntax bounds."""
    return bool(challenge and PKCE_CODE_CHALLENGE_RE.fullmatch(challenge))


def valid_logo_url(url):
    """Accept only https URLs or paths under this server's /static/ folder."""
    if not isinstance(url, str) or not url or len(url) > 500:
        return False
    if url.startswith("/static/"):
        return ".." not in url and "//" not in url and "\\" not in url
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def valid_pkce_code_verifier(verifier):
    """Validate a PKCE code verifier per RFC 7636 syntax bounds."""
    return bool(verifier and PKCE_CODE_VERIFIER_RE.fullmatch(verifier))


def issue_token(user, client, token_use, expires_in, scope, nonce=None):
    now = datetime.now(UTC)
    claims = {
        "iss": issuer(),
        "aud": client.client_id if token_use == "id_token" else client.audience,
        "sub": str(user.id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": secrets.token_urlsafe(24),
        "token_use": token_use,
        "scope": scope,
    }
    if token_use == "id_token" and "email" in scope.split():
        claims.update(
            {"email": user.email, "email_verified": bool(user.email_verified)}
        )
    if token_use == "id_token" and "profile" in scope.split():
        claims["name"] = user.name
    if token_use == "id_token" and nonce:
        claims["nonce"] = nonce
    if token_use == "id_token":
        claims["azp"] = client.client_id
    if token_use == "access_token":
        # Client identity lets a resource server tell which application within
        # an audience issued the call.  app_access/app_roles are a point-in-time
        # convenience for client UI only - enforcement re-reads the database.
        from gefapi.utils.app_access import active_app_keys, active_app_roles

        claims["azp"] = client.client_id
        claims["client_id"] = client.client_id
        claims["role"] = user.role
        claims["app_access"] = active_app_keys(user)
        claims["app_roles"] = active_app_roles(user)
    token = jwt.encode(
        {"alg": "RS256", "kid": key_id(), "typ": "JWT"},
        claims,
        _private_keys()[key_id()],
    )
    return token.decode("ascii") if isinstance(token, bytes) else token


def decode_token(token, token_use=None, audience=None):
    """Verify a JWT with Authlib's JOSE and registered issuer/key set."""
    options = {
        "iss": {"essential": True, "value": issuer()},
        "sub": {"essential": True},
        "aud": {"essential": True},
        "iat": {"essential": True},
        "exp": {"essential": True},
    }
    if audience:
        options["aud"] = {"essential": True, "value": audience}
    claims = jwt.decode(token, _jwks(), claims_options=options)
    claims.validate()
    if token_use and claims.get("token_use") != token_use:
        raise ValueError("wrong token type")
    return dict(claims)


def access_token_client(claims):
    """Resolve the active OAuth client named by a signed access token."""
    client_id = claims.get("client_id")
    authorized_party = claims.get("azp")
    if client_id and authorized_party and client_id != authorized_party:
        return None
    client_id = client_id or authorized_party
    if client_id:
        return OAuthClient.query.filter_by(
            client_id=client_id, is_active=True
        ).one_or_none()

    audience = claims.get("aud")
    if isinstance(audience, str):
        matches = (
            OAuthClient.query.filter_by(audience=audience, is_active=True)
            .limit(2)
            .all()
        )
        if len(matches) == 1:
            return matches[0]
    return None


def create_client(
    name,
    client_id,
    redirect_uris,
    audience,
    scopes,
    is_public=True,
    client_secret=None,
    post_logout_redirect_uris="",
    required_app_key=None,
    logo_url=None,
):
    client = OAuthClient(
        name=name,
        client_id=client_id,
        redirect_uris="\n".join(redirect_uris),
        post_logout_redirect_uris="\n".join(post_logout_redirect_uris),
        audience=audience,
        scopes=" ".join(scopes),
        is_public=is_public,
        required_app_key=required_app_key,
        logo_url=logo_url,
        client_secret_hash=generate_password_hash(client_secret)
        if client_secret
        else None,
    )
    db.session.add(client)
    db.session.commit()
    return client


def new_authorization_code(user, client, redirect_uri, challenge, scope, nonce):
    raw_code = secrets.token_urlsafe(48)
    code = AuthorizationCode(
        code_hash=OAuthClient.hash_code(raw_code),
        client_id=client.client_id,
        user_id=user.id,
        redirect_uri=redirect_uri,
        code_challenge=challenge,
        scope=scope,
        nonce=nonce,
        expires_at=datetime.now(UTC).replace(tzinfo=None)
        + timedelta(seconds=SETTINGS.get("OIDC_AUTHORIZATION_CODE_SECONDS", 300)),
    )
    db.session.add(code)
    db.session.commit()
    return raw_code


def consume_authorization_code(raw_code, client_id, redirect_uri, verifier):
    code = (
        AuthorizationCode.query.filter_by(code_hash=OAuthClient.hash_code(raw_code))
        .with_for_update()
        .first()
    )
    if (
        not code
        or not code.is_valid()
        or code.client_id != client_id
        or code.redirect_uri != redirect_uri
    ):
        return None
    if not valid_pkce_code_verifier(verifier):
        return None
    expected = create_s256_code_challenge(verifier)
    if code.code_challenge_method != "S256" or not hmac.compare_digest(
        expected, code.code_challenge
    ):
        return None
    code.used_at = datetime.now(UTC).replace(tzinfo=None)
    db.session.commit()
    return code


def _refresh_token_hash(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_oidc_refresh_token(user, client, scope, commit=True, family_id=None):
    """Create an OIDC refresh token without storing its bearer value."""
    raw_token = secrets.token_urlsafe(32)
    token = OIDCRefreshToken(
        user_id=user.id,
        client_id=client.client_id,
        token_hash=_refresh_token_hash(raw_token),
        family_id=family_id or uuid.uuid4(),
        scope=scope,
        expires_at=datetime.now(UTC).replace(tzinfo=None)
        + timedelta(days=SETTINGS.get("OIDC_REFRESH_TOKEN_DAYS", 30)),
    )
    db.session.add(token)
    if commit:
        db.session.commit()
    return raw_token


def rotate_oidc_refresh_token(raw_token, client, requested_scope=None):
    """Atomically consume and replace a client-bound OIDC refresh token."""
    token = (
        OIDCRefreshToken.query.filter_by(
            token_hash=_refresh_token_hash(raw_token), client_id=client.client_id
        )
        .with_for_update()
        .first()
    )
    if token and token.is_revoked:
        OIDCRefreshToken.query.filter_by(
            user_id=token.user_id,
            client_id=client.client_id,
            family_id=token.family_id,
            is_revoked=False,
        ).update({"is_revoked": True}, synchronize_session=False)
        db.session.commit()
        return None
    if not token or not token.is_valid():
        return None
    if not token.user or not token.user.is_active:
        return None

    scope = requested_scope or token.scope
    if not set(scope.split()).issubset(set(token.scope.split())):
        return None

    token.revoke()
    replacement = create_oidc_refresh_token(
        token.user, client, scope, commit=False, family_id=token.family_id
    )
    db.session.commit()
    return replacement, token.user, scope


def revoke_oidc_refresh_token_for_client(raw_token, client_id):
    """Revoke an OIDC refresh token only when it belongs to the caller's client."""
    token = OIDCRefreshToken.query.filter_by(
        token_hash=_refresh_token_hash(raw_token), client_id=client_id
    ).first()
    if not token:
        return False
    token.revoke()
    db.session.commit()
    return True


def revoke_oidc_refresh_token(raw_token):
    """Revoke an OIDC refresh token by its one-way stored hash."""
    token = OIDCRefreshToken.query.filter_by(
        token_hash=_refresh_token_hash(raw_token)
    ).first()
    if not token:
        return False
    token.revoke()
    db.session.commit()
    return True


def revoke_all_oidc_refresh_tokens(user_id):
    """Revoke every OIDC refresh token belonging to a user."""
    revoked = OIDCRefreshToken.query.filter_by(
        user_id=user_id, is_revoked=False
    ).update({"is_revoked": True}, synchronize_session=False)
    db.session.commit()
    return revoked


def revoke_oidc_refresh_tokens_for_user_and_client(user_id, client_id):
    """Revoke a user's OIDC refresh tokens issued to a single client."""
    revoked = OIDCRefreshToken.query.filter_by(
        user_id=user_id, client_id=client_id, is_revoked=False
    ).update({"is_revoked": True}, synchronize_session=False)
    db.session.commit()
    return revoked
