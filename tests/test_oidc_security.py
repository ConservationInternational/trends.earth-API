"""Security and compatibility tests for the Trends.Earth OIDC provider."""

import hashlib

from flask_jwt_extended import create_access_token

from gefapi import db, user_lookup_callback
from gefapi.config import SETTINGS
from gefapi.models import OAuthClient, OIDCRefreshToken, PasswordResetToken
from gefapi.models.refresh_token import RefreshToken
from gefapi.routes.oidc import _access_token_seconds
from gefapi.services.oidc_service import (
    TRENDS_API_AUDIENCE,
    access_token_client,
    create_oidc_refresh_token,
    decode_token,
    issue_token,
    rotate_oidc_refresh_token,
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


def test_oidc_access_token_lifetime_is_capped(monkeypatch):
    monkeypatch.setitem(SETTINGS, "OIDC_ACCESS_TOKEN_SECONDS", 3600)

    assert _access_token_seconds() == 300


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
