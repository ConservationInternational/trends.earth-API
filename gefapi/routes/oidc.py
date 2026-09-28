"""OIDC/OAuth endpoints used by first-party applications such as Rio."""

import html
import logging
import secrets
import time
from urllib.parse import urlencode

from authlib.jose.errors import JoseError
from flask import Blueprint, jsonify, redirect, request, session
from flask_jwt_extended import current_user, get_jwt, jwt_required

from gefapi import db, limiter
from gefapi.config import SETTINGS
from gefapi.errors import AccountLockedError
from gefapi.models import OAuthClient, User
from gefapi.services.oidc_service import (
    access_token_client,
    consume_authorization_code,
    create_client,
    create_oidc_refresh_token,
    decode_token,
    issue_token,
    issuer,
    jwks,
    new_authorization_code,
    revoke_all_oidc_refresh_tokens,
    revoke_oidc_refresh_token_for_client,
    rotate_oidc_refresh_token,
    valid_pkce_code_challenge,
)
from gefapi.services.user_service import UserService
from gefapi.utils.app_access import app_label, has_app_access, valid_app_key
from gefapi.utils.permissions import is_admin_or_higher
from gefapi.utils.scopes import require_scope
from gefapi.utils.security_events import log_security_event

oidc = Blueprint("oidc", __name__)

logger = logging.getLogger(__name__)

MAX_ACCESS_TOKEN_SECONDS = 300


def _access_token_seconds():
    configured = SETTINGS.get("OIDC_ACCESS_TOKEN_SECONDS", MAX_ACCESS_TOKEN_SECONDS)
    return max(1, min(configured, MAX_ACCESS_TOKEN_SECONDS))


def _oauth_error(error, description, status=400):
    return jsonify(error=error, error_description=description), status


def _app_access_denied_redirect(user, client, redirect_uri, state):
    """Record an access request and bounce the user back to the application.

    Returns ``None`` when the client is ungated or the user already has access.

    The pending request is created *before* the redirect, so self-service
    provisioning and an ``access_denied`` response are not in tension: the
    application reads ``te_app_access`` to decide whether to show "request
    submitted, awaiting approval" or "access removed".
    """
    app_key = client.required_app_key
    if not app_key or has_app_access(user, app_key):
        return None

    from gefapi.services.app_access_service import AppAccessError, AppAccessService

    access_state = "pending"
    try:
        AppAccessService.request_access(user, app_key)
    except AppAccessError as exc:
        access_state = exc.payload.get("status", "denied")
    except Exception:
        logger.exception("Failed to record app access request for %s", app_key)

    label = app_label(app_key)
    if access_state == "pending":
        description = f"Access to {label} is pending administrator approval."
    else:
        description = (
            f"Access to {label} has been removed. Please contact an administrator."
        )
    log_security_event(
        "APP_ACCESS_DENIED",
        user_id=str(user.id),
        user_email=user.email,
        details={"app_key": app_key, "client_id": client.client_id},
        level="info",
    )
    params = {
        "error": "access_denied",
        "error_description": description,
        "te_app_access": access_state,
        "te_app_key": app_key,
    }
    if state:
        params["state"] = state
    return redirect(f"{redirect_uri}?{urlencode(params)}")


def _client(data):
    return OAuthClient.query.filter_by(
        client_id=data.get("client_id"), is_active=True
    ).first()


def _validated_request(data):
    client = _client(data)
    redirect_uri = data.get("redirect_uri")
    if not client or not client.matches_redirect_uri(redirect_uri):
        return None, _oauth_error("invalid_request", "Unknown client or redirect URI")
    requested = (data.get("scope") or "openid").split()
    if "openid" not in requested:
        return None, _oauth_error("invalid_scope", "openid scope is required")
    if not set(requested).issubset(set(client.scopes.split())):
        return None, _oauth_error("invalid_scope", "Requested scope is not registered")
    if data.get("response_type") != "code":
        return None, _oauth_error("unsupported_response_type", "Only code is supported")
    if data.get("code_challenge_method") != "S256" or not valid_pkce_code_challenge(
        data.get("code_challenge")
    ):
        return None, _oauth_error("invalid_request", "S256 PKCE is required")
    if not data.get("state") or not data.get("nonce"):
        return None, _oauth_error("invalid_request", "state and nonce are required")
    return (client, redirect_uri, " ".join(requested)), None


@oidc.post("/api/v1/admin/oidc-clients")
@jwt_required()
@require_scope("client:manage")
def register_client():
    """Register an OIDC client; only API administrators may provision clients."""
    if not is_admin_or_higher(current_user):
        return jsonify(error="forbidden"), 403
    data = request.get_json(silent=True) or {}
    required = ("client_id", "name", "redirect_uris", "audience")
    if any(not data.get(field) for field in required):
        return _oauth_error(
            "invalid_request",
            "client_id, name, redirect_uris, and audience are required",
        )
    if OAuthClient.query.filter_by(client_id=data["client_id"]).first():
        return _oauth_error("invalid_request", "client_id is already registered", 409)
    is_public = bool(data.get("is_public", True))
    client_secret = secrets.token_urlsafe(32) if not is_public else None
    redirect_uris = data["redirect_uris"]
    if isinstance(redirect_uris, str):
        redirect_uris = [redirect_uris]
    post_logout_redirect_uris = data.get("post_logout_redirect_uris", [])
    if isinstance(post_logout_redirect_uris, str):
        post_logout_redirect_uris = [post_logout_redirect_uris]
    scopes = data.get("scopes", ["openid", "email", "profile"])
    if isinstance(scopes, str):
        scopes = scopes.split()
    required_app_key = data.get("required_app_key") or None
    if required_app_key and not valid_app_key(required_app_key):
        return _oauth_error(
            "invalid_request",
            f"Unknown required_app_key '{required_app_key}'",
        )
    client = create_client(
        name=data["name"],
        client_id=data["client_id"],
        redirect_uris=redirect_uris,
        post_logout_redirect_uris=post_logout_redirect_uris,
        audience=data["audience"],
        scopes=scopes,
        is_public=is_public,
        client_secret=client_secret,
        required_app_key=required_app_key,
    )
    response = {
        "client_id": client.client_id,
        "audience": client.audience,
        "scopes": client.scopes.split(),
        "required_app_key": client.required_app_key,
    }
    if client_secret:
        response["client_secret"] = client_secret
    return jsonify(data=response), 201


@oidc.get("/.well-known/openid-configuration")
def discovery():
    base = issuer()
    return jsonify(
        issuer=base,
        authorization_endpoint=f"{base}/oauth/authorize",
        token_endpoint=f"{base}/oauth/token",
        refresh_endpoint=f"{base}/oauth/refresh",
        userinfo_endpoint=f"{base}/oauth/userinfo",
        revocation_endpoint=f"{base}/oauth/revoke",
        jwks_uri=f"{base}/.well-known/jwks.json",
        response_types_supported=["code"],
        grant_types_supported=["authorization_code", "refresh_token"],
        subject_types_supported=["public"],
        id_token_signing_alg_values_supported=["RS256"],
        token_endpoint_auth_methods_supported=["none", "client_secret_post"],
        scopes_supported=["openid", "email", "profile"],
        claims_supported=[
            "iss",
            "aud",
            "sub",
            "azp",
            "client_id",
            "email",
            "email_verified",
            "name",
            "role",
            "app_access",
            "app_roles",
            "iat",
            "exp",
        ],
    )


@oidc.get("/.well-known/jwks.json")
def keys():
    response = jsonify(jwks())
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@oidc.route("/oauth/authorize", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def authorize():
    data = request.args if request.method == "GET" else request.form
    validated, error = _validated_request(data)
    if error:
        return error
    client, redirect_uri, scope = validated

    session_user_id = session.get("oidc_user_id")
    if session_user_id:
        session_user = db.session.get(User, session_user_id)
        if (
            not session_user
            or not session_user.is_active
            or session.get("oidc_auth_version") != session_user.auth_version
        ):
            session.pop("oidc_user_id", None)
            session.pop("oidc_auth_version", None)

    if request.method == "GET" and not session.get("oidc_user_id"):
        login_csrf = secrets.token_urlsafe(32)
        session["oidc_login_csrf"] = login_csrf
        fields = {
            key: data.get(key, "")
            for key in (
                "response_type",
                "client_id",
                "redirect_uri",
                "scope",
                "state",
                "code_challenge",
                "code_challenge_method",
                "nonce",
            )
        }
        fields["login_csrf"] = login_csrf
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
            for k, v in fields.items()
        )
        return (
            (
                f"<form method='post'><h1>Sign in to Trends.Earth</h1>{hidden}"
                "<input name='email' type='email' required>"
                "<input name='password' type='password' required>"
                "<button>Continue</button></form>"
            ),
            200,
        )
    if request.method == "POST" and not session.get("oidc_user_id"):
        login_csrf = session.pop("oidc_login_csrf", None)
        if not login_csrf or not secrets.compare_digest(
            login_csrf, data.get("login_csrf", "")
        ):
            return "Invalid login request", 400
        try:
            user = UserService.authenticate_user(
                (data.get("email") or "").strip().lower(),
                data.get("password") or "",
            )
        except AccountLockedError:
            return "Invalid credentials", 401
        if not user:
            return "Invalid credentials", 401
        session.clear()
        session["oidc_user_id"] = str(user.id)
        session["oidc_auth_version"] = user.auth_version
    user = db.session.get(User, session.get("oidc_user_id"))
    if not user or not user.is_active:
        session.pop("oidc_user_id", None)
        session.pop("oidc_auth_version", None)
        return "Account disabled", 403
    if not user.email_verified:
        return "Email verification is required", 403
    denied = _app_access_denied_redirect(user, client, redirect_uri, data.get("state"))
    if denied is not None:
        return denied
    code = new_authorization_code(
        user, client, redirect_uri, data["code_challenge"], scope, data.get("nonce")
    )
    params = {"code": code}
    if data.get("state"):
        params["state"] = data["state"]
    return redirect(f"{redirect_uri}?{urlencode(params)}")


@oidc.post("/oauth/token")
@limiter.limit("30 per minute")
def token():
    data = request.form if request.form else (request.get_json(silent=True) or {})
    grant_type = data.get("grant_type") or (
        "refresh_token" if request.path == "/oauth/refresh" else None
    )
    client = _client(data)
    if not client or (
        not client.is_public and not client.verify_secret(data.get("client_secret", ""))
    ):
        return _oauth_error("invalid_client", "Client authentication failed", 401)
    if grant_type == "authorization_code":
        code = consume_authorization_code(
            data.get("code", ""),
            client.client_id,
            data.get("redirect_uri", ""),
            data.get("code_verifier", ""),
        )
        if not code:
            return _oauth_error("invalid_grant", "Authorization code is invalid")
        user = db.session.get(User, code.user_id)
        if not user or not user.is_active:
            return _oauth_error("invalid_grant", "User account is disabled")
        if client.required_app_key and not has_app_access(
            user, client.required_app_key
        ):
            return _oauth_error(
                "access_denied",
                f"Access to {app_label(client.required_app_key)} is not activated",
            )
        expires_in = _access_token_seconds()
        access = issue_token(user, client, "access_token", expires_in, code.scope)
        identity = issue_token(
            user, client, "id_token", expires_in, code.scope, code.nonce
        )
        refresh = create_oidc_refresh_token(user, client, code.scope)
        return jsonify(
            access_token=access,
            id_token=identity,
            refresh_token=refresh,
            token_type="Bearer",
            expires_in=expires_in,
            scope=code.scope,
        )
    if grant_type == "refresh_token":
        result = rotate_oidc_refresh_token(
            data.get("refresh_token", ""), client, data.get("scope")
        )
        if not result:
            return _oauth_error("invalid_grant", "Refresh token is invalid")
        refresh, user, scope = result
        if not user.is_active:
            return _oauth_error("invalid_grant", "User account is disabled")
        if client.required_app_key and not has_app_access(
            user, client.required_app_key
        ):
            return _oauth_error(
                "access_denied",
                f"Access to {app_label(client.required_app_key)} is not activated",
            )
        expires_in = _access_token_seconds()
        access = issue_token(user, client, "access_token", expires_in, scope)
        # No id_token on refresh: it asserts an authentication event, not a
        # session credential, and OIDC Core 12.2 would require replaying the
        # original nonce. Clients use the access token and /oauth/userinfo.
        return jsonify(
            access_token=access,
            refresh_token=refresh,
            token_type="Bearer",
            expires_in=expires_in,
            scope=scope,
        )
    return _oauth_error(
        "unsupported_grant_type",
        "Only authorization_code and refresh_token are supported",
    )


@oidc.post("/oauth/refresh")
def refresh():
    """Compatibility alias for refresh-token clients using the plan contract."""
    return token()


@oidc.post("/oauth/revoke")
def revoke():
    from gefapi import add_token_to_blocklist

    data = request.form if request.form else (request.get_json(silent=True) or {})
    client = _client(data)
    if not client or (
        not client.is_public and not client.verify_secret(data.get("client_secret", ""))
    ):
        return _oauth_error("invalid_client", "Client authentication failed", 401)
    if data.get("token"):
        raw_token = data["token"]
        try:
            claims = decode_token(raw_token, "access_token", audience=client.audience)
            add_token_to_blocklist(
                claims["jti"], max(int(claims["exp"] - time.time()), 0) + 60
            )
        except (JoseError, ValueError, TypeError):
            revoke_oidc_refresh_token_for_client(raw_token, client.client_id)
    return "", 200


@oidc.get("/oauth/userinfo")
def userinfo():
    from gefapi import is_token_in_blocklist

    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    try:
        claims = decode_token(token, "access_token")
        client = access_token_client(claims)
        if not client:
            raise JoseError("unknown or ambiguous OAuth client")
        claims = decode_token(token, "access_token", audience=client.audience)
        if "openid" not in claims.get("scope", "").split():
            raise JoseError("openid scope is required")
        if is_token_in_blocklist(claims.get("jti")):
            raise JoseError("revoked token")
    except (JoseError, ValueError, TypeError):
        return _oauth_error("invalid_token", "Access token is invalid", 401)
    user = db.session.get(User, claims["sub"])
    if not user or not user.is_active:
        return _oauth_error("invalid_token", "User account is disabled", 401)
    response = {"sub": str(user.id)}
    scope = set(claims.get("scope", "").split())
    if "email" in scope:
        response.update(
            email=user.email,
            email_verified=bool(user.email_verified),
        )
    if "profile" in scope:
        response["name"] = user.name
    return jsonify(response)


@oidc.post("/oauth/logout")
def logout():
    data = request.form if request.form else (request.get_json(silent=True) or {})
    redirect_uri = data.get("post_logout_redirect_uri")
    client = _client({"client_id": data.get("client_id")})
    if redirect_uri and (
        not client or redirect_uri not in client.allowed_post_logout_redirect_uris()
    ):
        return _oauth_error("invalid_request", "Unknown post-logout redirect URI")
    user_id = session.get("oidc_user_id")
    id_token_hint = data.get("id_token_hint")
    if user_id and id_token_hint and client:
        try:
            claims = decode_token(id_token_hint, "id_token", audience=client.client_id)
            if claims.get("sub") == user_id:
                revoke_all_oidc_refresh_tokens(user_id)
        except (JoseError, ValueError, TypeError, KeyError):
            pass
    access_token = (
        request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    if access_token:
        try:
            from gefapi import add_token_to_blocklist

            claims = decode_token(access_token, "access_token")
            add_token_to_blocklist(
                claims["jti"], max(int(claims["exp"] - time.time()), 0) + 60
            )
        except (JoseError, ValueError, TypeError, KeyError):
            pass
    session.clear()
    return redirect(redirect_uri) if redirect_uri else jsonify(message="Logged out")


@oidc.get("/api/v1/admin/users/<subject>")
@jwt_required()
def admin_user_lookup(subject):
    claims = get_jwt()
    if (
        claims.get("grant_type") != "client_credentials"
        or "user:read" not in (claims.get("scopes") or "").split()
    ):
        return jsonify(error="insufficient_scope"), 403
    if not is_admin_or_higher(current_user):
        return jsonify(error="forbidden"), 403
    user = db.session.get(User, subject)
    if not user:
        return jsonify(error="not_found"), 404
    return jsonify(
        sub=str(user.id),
        email=user.email,
        email_verified=bool(user.email_verified),
        name=user.name,
        disabled=not user.is_active,
    )
