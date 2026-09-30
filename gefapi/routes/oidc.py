"""OIDC/OAuth endpoints used by first-party applications such as Rio."""

import logging
import secrets
import time
from urllib.parse import urlencode

from authlib.jose.errors import JoseError
from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_babel import gettext as _
from flask_babel import ngettext
from flask_jwt_extended import current_user, get_jwt, jwt_required

from gefapi import db, limiter
from gefapi.config import SETTINGS
from gefapi.errors import (
    AccountLockedError,
    EmailError,
    PasswordValidationError,
    UserDuplicated,
    UserNotFound,
)
from gefapi.i18n import SUPPORTED_LANGUAGES
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
    valid_logo_url,
    valid_pkce_code_challenge,
)
from gefapi.services.user_service import UserService
from gefapi.utils.app_access import app_label, has_app_access, valid_app_key
from gefapi.utils.permissions import is_admin_or_higher
from gefapi.utils.scopes import require_scope
from gefapi.utils.security_events import log_security_event
from gefapi.validators import (
    validate_country,
    validate_email,
    validate_institution,
    validate_name,
)

oidc = Blueprint("oidc", __name__)

logger = logging.getLogger(__name__)

MAX_ACCESS_TOKEN_SECONDS = 300

AUTHORIZE_PARAMS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "scope",
    "state",
    "code_challenge",
    "code_challenge_method",
    "nonce",
    "ui_locales",
)
DEFAULT_LOGO = "auth/trends_earth_logo_from_CI.png"
PRIVACY_POLICY_URL = "https://www.conservation.org/policies/privacy"
TERMS_OF_USE_URL = "https://www.conservation.org/policies/terms-of-use"


@oidc.after_request
def prevent_token_caching(response):
    if request.endpoint in {"oidc.token", "oidc.refresh"}:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


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
        description = _(
            "Access to %(app)s is pending administrator approval.", app=label
        )
    else:
        description = _(
            "Access to %(app)s has been removed. Please contact an administrator.",
            app=label,
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


def _new_form_csrf(session_key):
    token = secrets.token_urlsafe(32)
    session[session_key] = token
    return token


def _form_csrf_valid(session_key, submitted):
    expected = session.pop(session_key, None)
    return bool(expected) and secrets.compare_digest(expected, submitted or "")


def _render_auth_page(template, client, data, status=200, **context):
    params = {
        key: data.get(key, "")
        for key in AUTHORIZE_PARAMS
        if key != "ui_locales" or data.get(key)
    }
    query = urlencode(params)
    page_endpoint = {
        "auth/register.html": "oidc.register",
        "auth/forgot_password.html": "oidc.forgot_password",
    }.get(template, "oidc.authorize")
    language_links = [
        (
            code,
            label,
            f"{url_for(page_endpoint)}?{urlencode({**params, 'ui_locales': code})}",
        )
        for code, label in SUPPORTED_LANGUAGES.items()
    ]
    return (
        render_template(
            template,
            client_name=client.name,
            logo_url=client.logo_url or url_for("static", filename=DEFAULT_LOGO),
            params=params,
            authorize_url=f"{url_for('oidc.authorize')}?{query}",
            register_url=f"{url_for('oidc.register')}?{query}",
            forgot_password_url=f"{url_for('oidc.forgot_password')}?{query}",
            privacy_url=PRIVACY_POLICY_URL,
            terms_url=TERMS_OF_USE_URL,
            language_links=language_links,
            **context,
        ),
        status,
    )


def _render_login(client, data, status=200, error=None):
    return _render_auth_page(
        "auth/login.html",
        client,
        data,
        status,
        csrf=_new_form_csrf("oidc_login_csrf"),
        email=data.get("email", ""),
        error=error,
    )


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
    logo_url = data.get("logo_url") or None
    if logo_url and not valid_logo_url(logo_url):
        return _oauth_error(
            "invalid_request", "logo_url must be an https URL or a /static/ path"
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
        logo_url=logo_url,
    )
    response = {
        "client_id": client.client_id,
        "audience": client.audience,
        "scopes": client.scopes.split(),
        "required_app_key": client.required_app_key,
        "logo_url": client.logo_url,
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
        ui_locales_supported=list(SUPPORTED_LANGUAGES),
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
        return _render_login(client, data)
    if request.method == "POST" and not session.get("oidc_user_id"):
        if not _form_csrf_valid("oidc_login_csrf", data.get("login_csrf")):
            return _render_login(
                client,
                data,
                400,
                error=_("Your sign-in form expired. Please try again."),
            )
        try:
            user = UserService.authenticate_user(
                (data.get("email") or "").strip().lower(),
                data.get("password") or "",
            )
        except AccountLockedError as exc:
            if exc.requires_password_reset or exc.minutes_remaining is None:
                message = _(
                    "This account is locked after too many failed sign-in "
                    "attempts. Reset your password to unlock it."
                )
            else:
                message = ngettext(
                    "This account is temporarily locked after too many failed "
                    "sign-in attempts. Please try again in %(num)d minute.",
                    "This account is temporarily locked after too many failed "
                    "sign-in attempts. Please try again in %(num)d minutes.",
                    exc.minutes_remaining,
                )
            return _render_login(client, data, 401, error=message)
        if not user:
            return _render_login(
                client, data, 401, error=_("Invalid email or password.")
            )
        session.clear()
        session["oidc_user_id"] = str(user.id)
        session["oidc_auth_version"] = user.auth_version
    user = db.session.get(User, session.get("oidc_user_id"))
    if not user or not user.is_active:
        session.pop("oidc_user_id", None)
        session.pop("oidc_auth_version", None)
        return _render_login(client, data, 403, error=_("This account is disabled."))
    if not user.email_verified:
        return _render_login(
            client,
            data,
            403,
            error=_("Please verify your email address before signing in."),
        )
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


@oidc.route("/oauth/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    """Hosted self-registration that returns the user to the client's sign-in."""
    data = request.args if request.method == "GET" else request.form
    validated, error = _validated_request(data)
    if error:
        return error
    client = validated[0]
    form = {
        key: (data.get(key) or "").strip()
        for key in ("name", "email", "country", "institution")
    }

    def render(status=200, **context):
        return _render_auth_page(
            "auth/register.html",
            client,
            data,
            status,
            csrf=_new_form_csrf("oidc_register_csrf"),
            form=form,
            **context,
        )

    if request.method == "GET":
        return render()
    if not _form_csrf_valid("oidc_register_csrf", data.get("register_csrf")):
        return render(400, error=_("Your registration form expired. Please try again."))
    try:
        user_data = {
            "name": validate_name(form["name"]),
            "email": validate_email(form["email"]),
            "role": "USER",
        }
        if form["country"]:
            user_data["country"] = validate_country(form["country"])
        if form["institution"]:
            user_data["institution"] = validate_institution(form["institution"])
    except ValueError as exc:
        # Validator messages are marked with N_() so they have catalog entries.
        return render(400, error=_(str(exc)))
    try:
        UserService.create_user(user_data)
    except UserDuplicated:
        # Same response as success so the form cannot enumerate accounts.
        pass
    except PasswordValidationError as exc:
        return render(400, error=_(exc.message))
    return render(registered=True)


@oidc.route("/oauth/forgot-password", methods=["GET", "POST"])
@limiter.limit("3 per hour", methods=["POST"])
def forgot_password():
    """Hosted password-reset request that returns the user to the client's sign-in."""
    data = request.args if request.method == "GET" else request.form
    validated, error = _validated_request(data)
    if error:
        return error
    client = validated[0]
    email = (data.get("email") or "").strip()

    def render(status=200, **context):
        return _render_auth_page(
            "auth/forgot_password.html",
            client,
            data,
            status,
            csrf=_new_form_csrf("oidc_forgot_csrf"),
            email=email,
            **context,
        )

    if request.method == "GET":
        return render()
    if not _form_csrf_valid("oidc_forgot_csrf", data.get("forgot_csrf")):
        return render(400, error=_("Your reset form expired. Please try again."))
    try:
        normalized = validate_email(email)
    except ValueError as exc:
        return render(400, error=_(str(exc)))
    try:
        UserService.recover_password(normalized)
    except UserNotFound:
        # Same response as success so the form cannot enumerate accounts.
        pass
    except EmailError:
        logger.exception("Password reset email failed for client %s", client.client_id)
    return render(sent=True)


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
