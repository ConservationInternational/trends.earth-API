"""Security and compatibility tests for the Trends.Earth OIDC provider."""

import datetime
import hashlib
from unittest.mock import patch

from flask_jwt_extended import create_access_token

from gefapi import db, user_lookup_callback
from gefapi.config import SETTINGS
from gefapi.models import OAuthClient, OIDCRefreshToken, PasswordResetToken, User
from gefapi.models.refresh_token import RefreshToken
from gefapi.routes.oidc import _access_token_seconds
from gefapi.services.oidc_service import (
    TRENDS_API_AUDIENCE,
    access_token_client,
    create_oidc_refresh_token,
    decode_token,
    issue_token,
    rotate_oidc_refresh_token,
    valid_logo_url,
)
from gefapi.services.refresh_token_service import RefreshTokenService
from gefapi.utils.scopes import _has_scope


def _oidc_client(client_id, audience):
    return OAuthClient(
        name=client_id,
        client_id=client_id,
        redirect_uris="https://rio.example.test/callback",
        audience=audience,
        scopes="openid email profile",
        is_public=True,
    )


def test_oidc_refresh_tokens_are_hashed_client_bound_and_single_use(app, regular_user):
    client_a = _oidc_client("rio-a", "rio-audience")
    client_b = _oidc_client("other-client", "other-audience")
    db.session.add_all([client_a, client_b])
    db.session.commit()

    raw_token = create_oidc_refresh_token(regular_user, client_a, "openid email")
    stored = OIDCRefreshToken.query.one()

    assert stored.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
    assert raw_token not in stored.token_hash
    assert rotate_oidc_refresh_token(raw_token, client_b) is None
    db.session.rollback()

    rotated = rotate_oidc_refresh_token(raw_token, client_a)
    assert rotated is not None
    replacement, user, scope = rotated
    assert user.id == regular_user.id
    assert scope == "openid email"
    assert replacement != raw_token
    assert rotate_oidc_refresh_token(raw_token, client_a) is None
    db.session.rollback()


def test_legacy_refresh_tokens_remain_usable(app, regular_user):
    legacy = RefreshTokenService.create_refresh_token(regular_user.id)
    stored_hash = legacy.token_hash

    assert stored_hash == RefreshToken.hash_token(legacy.token)
    assert legacy._token is None

    validated, user = RefreshTokenService.validate_refresh_token(legacy.token)

    assert validated.id == legacy.id
    assert user.id == regular_user.id


def test_password_reset_tokens_are_hashed_at_rest(app, regular_user):
    reset_token = PasswordResetToken(user_id=regular_user.id)
    raw_token = reset_token.token
    db.session.add(reset_token)
    db.session.commit()

    stored = PasswordResetToken.query.one()
    assert stored.token_hash == PasswordResetToken.hash_token(raw_token)
    assert stored._token is None
    assert PasswordResetToken.get_valid_token(raw_token).id == stored.id


def test_inactive_users_and_stale_auth_versions_fail_jwt_lookup(app, regular_user):
    user = db.session.merge(regular_user)
    claims = {"sub": str(user.id), "auth_version": user.auth_version}
    assert user_lookup_callback({}, claims).id == user.id

    user.is_active = False
    db.session.commit()
    assert user_lookup_callback({}, claims) is None


def test_service_client_scope_cannot_impersonate_full_access():
    assert (
        _has_scope(
            "client:manage",
            {"grant_type": "client_credentials", "scopes": "execution:read"},
        )
        is False
    )
    assert (
        _has_scope(
            "execution:read",
            {"grant_type": "client_credentials", "scopes": "execution:read"},
        )
        is True
    )


def test_id_token_audience_is_client_id_not_resource_audience(app, regular_user):
    client = _oidc_client("rio-client", "rio-resource")

    token = issue_token(regular_user, client, "id_token", 300, "openid", "nonce")

    claims = decode_token(token, "id_token", audience="rio-client")
    assert claims["aud"] == "rio-client"
    assert claims["azp"] == "rio-client"


def test_shared_resource_audience_still_resolves_the_access_token_client(
    app, client, regular_user
):
    api_ui = _oidc_client("te-api-ui", "trends-earth-api-ui")
    qgis_plugin = _oidc_client("te-qgis-plugin", TRENDS_API_AUDIENCE)
    te_web = _oidc_client("te-web", TRENDS_API_AUDIENCE)
    db.session.add_all([api_ui, qgis_plugin, te_web])
    db.session.commit()

    token = issue_token(regular_user, te_web, "access_token", 300, "openid")
    claims = decode_token(token, "access_token", audience=TRENDS_API_AUDIENCE)

    assert claims["aud"] == TRENDS_API_AUDIENCE
    assert api_ui.audience != qgis_plugin.audience == te_web.audience
    assert access_token_client(claims).client_id == "te-web"
    assert access_token_client({"aud": TRENDS_API_AUDIENCE}) is None
    assert (
        access_token_client(
            {"aud": TRENDS_API_AUDIENCE, "client_id": "te-api-ui", "azp": "te-web"}
        )
        is None
    )

    response = client.get(
        "/oauth/userinfo", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.get_json()["sub"] == str(regular_user.id)


def test_admin_can_register_client_with_an_existing_resource_audience(
    app, client, admin_user
):
    db.session.add(_oidc_client("te-qgis-plugin", TRENDS_API_AUDIENCE))
    db.session.commit()
    token = create_access_token(
        identity=admin_user.id,
        additional_claims={
            "grant_type": "client_credentials",
            "scopes": "client:manage",
        },
    )

    response = client.post(
        "/api/v1/admin/oidc-clients",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "client_id": "te-web",
            "name": "Trends.Earth Web",
            "redirect_uris": ["https://trends.earth/auth/callback"],
            "post_logout_redirect_uris": ["https://trends.earth/"],
            "audience": TRENDS_API_AUDIENCE,
            "scopes": ["openid", "email", "profile"],
            "is_public": True,
        },
    )

    assert response.status_code == 201
    assert response.get_json()["data"]["audience"] == TRENDS_API_AUDIENCE


def test_invalid_pkce_challenge_is_rejected(client):
    oidc_client = _oidc_client("rio-pkce", "rio-resource")
    db.session.add(oidc_client)
    db.session.commit()

    response = client.get(
        "/oauth/authorize",
        query_string={
            "client_id": "rio-pkce",
            "redirect_uri": "https://rio.example.test/callback",
            "response_type": "code",
            "scope": "openid",
            "state": "state-value",
            "nonce": "nonce-value",
            "code_challenge_method": "S256",
            "code_challenge": "too-short",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


def test_login_form_preserves_response_type(client):
    db.session.add(_oidc_client("rio-form", "rio-resource"))
    db.session.commit()

    response = client.get(
        "/oauth/authorize",
        query_string={
            "client_id": "rio-form",
            "redirect_uri": "https://rio.example.test/callback",
            "response_type": "code",
            "scope": "openid",
            "state": "state-value",
            "nonce": "nonce-value",
            "code_challenge_method": "S256",
            "code_challenge": "A" * 43,
        },
    )

    assert response.status_code == 200
    assert b'name="response_type" value="code"' in response.data


def _authorize_query(client_id):
    return {
        "client_id": client_id,
        "redirect_uri": "https://rio.example.test/callback",
        "response_type": "code",
        "scope": "openid",
        "state": "state-value",
        "nonce": "nonce-value",
        "code_challenge_method": "S256",
        "code_challenge": "A" * 43,
    }


def test_login_page_uses_client_logo_and_shared_footer(client):
    branded = _oidc_client("rio-logo", "rio-resource")
    branded.logo_url = "https://rio.example.test/logo.png"
    db.session.add_all([branded, _oidc_client("plain-logo", "plain-resource")])
    db.session.commit()

    page = client.get("/oauth/authorize", query_string=_authorize_query("rio-logo"))
    default = client.get(
        "/oauth/authorize", query_string=_authorize_query("plain-logo")
    )

    assert b'src="https://rio.example.test/logo.png"' in page.data
    assert b"/static/auth/trends_earth_logo_from_CI.png" in default.data
    for body in (page.data, default.data):
        assert b"Powered by" in body
        assert b"/static/auth/trends_earth_bl_print.png" in body
        assert b"Privacy Policy" in body
        assert b"Terms of Use" in body
        assert b"/oauth/register?" in body


def test_invalid_credentials_rerender_login_form(client):
    db.session.add(_oidc_client("rio-badpw", "rio-resource"))
    db.session.commit()
    with client.session_transaction() as session:
        session["oidc_login_csrf"] = "csrf"

    response = client.post(
        "/oauth/authorize",
        data={
            **_authorize_query("rio-badpw"),
            "login_csrf": "csrf",
            "email": "nobody@example.test",
            "password": "wrong",
        },
    )

    assert response.status_code == 401
    assert b"Invalid email or password." in response.data
    assert b'value="nobody@example.test"' in response.data


def _post_login(client, client_id, email):
    with client.session_transaction() as session:
        session["oidc_login_csrf"] = "csrf"
    return client.post(
        "/oauth/authorize",
        data={
            **_authorize_query(client_id),
            "login_csrf": "csrf",
            "email": email,
            "password": "wrong",
        },
    )


def test_locked_account_shows_remaining_lockout_time(client, regular_user):
    db.session.add(_oidc_client("rio-locked", "rio-resource"))
    User.query.filter_by(id=regular_user.id).update(
        {
            "failed_login_count": 5,
            "locked_until": datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            + datetime.timedelta(minutes=15),
        }
    )
    db.session.commit()

    response = _post_login(client, "rio-locked", regular_user.email)

    assert response.status_code == 401
    body = response.get_data(as_text=True)
    assert "temporarily locked" in body
    assert "try again in 15 minutes" in body or "try again in 14 minutes" in body
    assert "Invalid email or password." not in body


def test_permanently_locked_account_points_to_password_reset(client, regular_user):
    db.session.add(_oidc_client("rio-locked-perm", "rio-resource"))
    User.query.filter_by(id=regular_user.id).update(
        {
            "failed_login_count": 20,
            "locked_until": datetime.datetime(2999, 1, 1, tzinfo=datetime.UTC),
        }
    )
    db.session.commit()

    response = _post_login(client, "rio-locked-perm", regular_user.email)

    assert response.status_code == 401
    body = response.get_data(as_text=True)
    assert "Reset your password to unlock it." in body
    assert "/oauth/forgot-password?" in body


def test_register_page_creates_user_and_hides_duplicates(client):
    db.session.add(_oidc_client("rio-register", "rio-resource"))
    db.session.commit()
    query = _authorize_query("rio-register")

    page = client.get("/oauth/register", query_string=query)
    assert page.status_code == 200
    assert b"Powered by" in page.data

    def submit():
        with client.session_transaction() as session:
            session["oidc_register_csrf"] = "csrf"
        return client.post(
            "/oauth/register",
            data={
                **query,
                "register_csrf": "csrf",
                "name": "New Person",
                "email": "New.Person@example.test",
                "institution": "Example Org",
            },
        )

    with patch("gefapi.services.email_service.EmailService.send_html_email"):
        first = submit()
        second = submit()

    assert first.status_code == 200
    assert b"Thanks for registering" in first.data
    assert second.status_code == 200
    assert b"Thanks for registering" in second.data
    assert User.query.filter_by(email="new.person@example.test").count() == 1


def test_register_rejects_missing_csrf_and_unknown_client(client):
    db.session.add(_oidc_client("rio-register-csrf", "rio-resource"))
    db.session.commit()

    no_csrf = client.post(
        "/oauth/register",
        data={
            **_authorize_query("rio-register-csrf"),
            "name": "X",
            "email": "x@example.test",
        },
    )
    unknown = client.get("/oauth/register", query_string=_authorize_query("nope"))

    assert no_csrf.status_code == 400
    assert User.query.filter_by(email="x@example.test").count() == 0
    assert unknown.status_code == 400


def test_forgot_password_sends_reset_and_hides_unknown_accounts(client, regular_user):
    branded = _oidc_client("rio-forgot", "rio-resource")
    branded.logo_url = "https://rio.example.test/logo.png"
    db.session.add(branded)
    db.session.commit()
    query = _authorize_query("rio-forgot")

    login = client.get("/oauth/authorize", query_string=query)
    assert b"/oauth/forgot-password?" in login.data

    page = client.get("/oauth/forgot-password", query_string=query)
    assert page.status_code == 200
    assert b'src="https://rio.example.test/logo.png"' in page.data
    assert b"Powered by" in page.data
    assert b"Privacy Policy" in page.data

    def submit(email):
        with client.session_transaction() as session:
            session["oidc_forgot_csrf"] = "csrf"
        return client.post(
            "/oauth/forgot-password",
            data={**query, "forgot_csrf": "csrf", "email": email},
        )

    with patch(
        "gefapi.services.email_service.EmailService.send_html_email"
    ) as send_email:
        known = submit(regular_user.email.upper())
        unknown = submit("nobody@example.test")

    assert known.status_code == unknown.status_code == 200
    assert b"If an account exists" in known.data
    assert b"If an account exists" in unknown.data
    assert send_email.call_count == 1
    assert send_email.call_args.kwargs["recipients"] == [regular_user.email]
    assert PasswordResetToken.query.filter_by(user_id=regular_user.id).count() == 1


def test_forgot_password_rejects_missing_csrf_and_unknown_client(client):
    db.session.add(_oidc_client("rio-forgot-csrf", "rio-resource"))
    db.session.commit()

    with patch(
        "gefapi.services.email_service.EmailService.send_html_email"
    ) as send_email:
        no_csrf = client.post(
            "/oauth/forgot-password",
            data={**_authorize_query("rio-forgot-csrf"), "email": "x@example.test"},
        )
    unknown = client.get(
        "/oauth/forgot-password", query_string=_authorize_query("nope")
    )

    assert no_csrf.status_code == 400
    assert send_email.call_count == 0
    assert unknown.status_code == 400


def test_logo_url_validation():
    assert valid_logo_url("https://cdn.example.test/logo.png")
    assert valid_logo_url("/static/auth/trends_earth_logo_from_CI.png")
    assert not valid_logo_url("http://cdn.example.test/logo.png")
    assert not valid_logo_url("javascript:alert(1)")
    assert not valid_logo_url("/static/../secret")
    assert not valid_logo_url("//evil.test/logo.png")


def test_oidc_access_token_lifetime_is_capped(monkeypatch):
    monkeypatch.setitem(SETTINGS, "OIDC_ACCESS_TOKEN_SECONDS", 3600)

    assert _access_token_seconds() == 300


def test_oidc_token_endpoints_prevent_response_caching(client):
    for path in ("/oauth/token", "/oauth/refresh"):
        response = client.post(path, data={"client_id": "unknown"})

        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Pragma"] == "no-cache"


def test_oidc_refresh_token_reuse_revokes_token_family(app, regular_user):
    client = _oidc_client("rio-family", "rio-resource")
    db.session.add(client)
    db.session.commit()

    original = create_oidc_refresh_token(regular_user, client, "openid email")
    rotated = rotate_oidc_refresh_token(original, client)
    assert rotated is not None
    replacement, _, _ = rotated

    assert rotate_oidc_refresh_token(original, client) is None
    db.session.rollback()
    assert rotate_oidc_refresh_token(replacement, client) is None


def test_revoke_endpoint_is_bound_to_calling_client(app, client, regular_user):
    client_a = _oidc_client("rio-revoke-a", "rio-resource-a")
    client_b = _oidc_client("rio-revoke-b", "rio-resource-b")
    db.session.add_all([client_a, client_b])
    db.session.commit()
    token = create_oidc_refresh_token(regular_user, client_a, "openid")

    response = client.post(
        "/oauth/revoke",
        data={"client_id": client_b.client_id, "token": token},
    )

    assert response.status_code == 200
    assert rotate_oidc_refresh_token(token, client_a) is not None


def test_admin_user_lookup_requires_admin_backed_service_token(
    app, client, regular_user, admin_user
):
    regular_token = create_access_token(
        identity=regular_user.id,
        additional_claims={"grant_type": "client_credentials", "scopes": "user:read"},
    )
    admin_token = create_access_token(
        identity=admin_user.id,
        additional_claims={"grant_type": "client_credentials", "scopes": "user:read"},
    )

    denied = client.get(
        f"/api/v1/admin/users/{admin_user.id}",
        headers={"Authorization": f"Bearer {regular_token}"},
    )
    allowed = client.get(
        f"/api/v1/admin/users/{regular_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert denied.status_code == 403
    assert denied.get_json()["error"] == "forbidden"
    assert allowed.status_code == 200
    assert allowed.get_json()["email"] == regular_user.email
