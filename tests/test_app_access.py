"""Tests for per-application access control (Phase 1).

Covers the access-control model, the OIDC gate, and the regression guarantees
that the QGIS plugin, API UI, and Avoided Emissions service client are
unaffected.
"""

from unittest.mock import patch
import urllib.parse

import pytest

from gefapi import db
from gefapi.models import AuthorizationCode, OAuthClient, OIDCRefreshToken, User
from gefapi.models.app_access import (
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REVOKED,
    UserAppAccess,
)
from gefapi.models.refresh_token import RefreshToken
from gefapi.services.app_access_service import AppAccessError, AppAccessService
from gefapi.services.oidc_service import create_oidc_refresh_token, issue_token
from gefapi.services.refresh_token_service import RefreshTokenService
from gefapi.utils.app_access import (
    active_app_keys,
    active_app_roles,
    app_role,
    has_app_access,
    require_app_access,
)
from tests.conftest import USER_TEST_PASSWORD

AE = "avoided_emissions"
RIO = "rio_coherence"


def _client(client_id, audience, required_app_key=None, redirect="https://x.test/cb"):
    client = OAuthClient(
        name=client_id,
        client_id=client_id,
        redirect_uris=redirect,
        post_logout_redirect_uris="",
        audience=audience,
        scopes="openid email profile",
        is_public=True,
        required_app_key=required_app_key,
    )
    db.session.add(client)
    db.session.commit()
    return client


def _grant(user, app_key, status=STATUS_ACTIVE, role="member"):
    grant = UserAppAccess(user_id=user.id, app_key=app_key, status=status, role=role)
    db.session.add(grant)
    db.session.commit()
    return grant


def _verified(user):
    """The authorize endpoint refuses unverified accounts."""
    User.query.filter_by(id=user.id).update({"email_verified": True})
    db.session.commit()
    return db.session.get(User, user.id)


# ---------------------------------------------------------------------------
# Access-control model
# ---------------------------------------------------------------------------


def test_no_row_means_no_access(app, regular_user):
    assert has_app_access(regular_user, AE) is False
    assert active_app_keys(regular_user) == []
    assert active_app_roles(regular_user) == {}
    assert app_role(regular_user, AE) is None


@pytest.mark.parametrize("status", [STATUS_PENDING, STATUS_REVOKED])
def test_pending_and_revoked_do_not_grant_access(app, regular_user, status):
    _grant(regular_user, AE, status=status)
    assert has_app_access(regular_user, AE) is False
    assert active_app_keys(regular_user) == []


def test_active_grant_exposes_role(app, regular_user):
    _grant(regular_user, AE, status=STATUS_ACTIVE, role="admin")
    assert has_app_access(regular_user, AE) is True
    assert app_role(regular_user, AE) == "admin"
    assert active_app_roles(regular_user) == {AE: "admin"}


def test_app_access_guard_binds_client_to_registered_app(app, regular_user):
    from flask import g

    _grant(regular_user, AE)
    ae_client = _client("ae-client", "ae-audience", required_app_key=AE)
    rio_client = _client("rio-client", "rio-audience", required_app_key=RIO)

    @require_app_access(AE)
    def protected_endpoint():
        return "ok"

    with app.test_request_context("/"):
        g.oidc_client = rio_client
        with patch("gefapi.utils.app_access.current_user", regular_user):
            denied = protected_endpoint()
        assert denied[1] == 403
        assert denied[0].get_json()["error"] == "client_app_mismatch"

    with app.test_request_context("/"):
        g.oidc_client = ae_client
        with patch("gefapi.utils.app_access.current_user", regular_user):
            assert protected_endpoint() == "ok"


def test_grant_is_unique_per_user_and_app(app, regular_user):
    _grant(regular_user, AE)
    duplicate = UserAppAccess(user_id=regular_user.id, app_key=AE)
    db.session.add(duplicate)
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


def test_grants_cascade_delete_with_user(app):
    user = User(
        email="cascade@example.com",
        password="Str0ngPassw0rd!",
        name="Cascade",
        country="US",
        institution="Test",
    )
    db.session.add(user)
    db.session.commit()
    _grant(user, AE)
    db.session.delete(user)
    db.session.commit()
    assert UserAppAccess.query.filter_by(user_id=user.id).count() == 0


def test_invalid_role_is_rejected(app, regular_user):
    with pytest.raises(AppAccessError) as exc:
        AppAccessService.set_access(regular_user.id, AE, STATUS_ACTIVE, role="wizard")
    assert "Invalid role" in exc.value.message


def test_unknown_app_key_is_rejected(app, regular_user):
    with pytest.raises(AppAccessError):
        AppAccessService.set_access(regular_user.id, "nope", STATUS_ACTIVE)


def test_role_change_preserves_granted_at(app, regular_user, admin_user):
    grant = AppAccessService.set_access(
        regular_user.id, AE, STATUS_ACTIVE, role="member", acting_user=admin_user
    )
    granted_at = grant.granted_at
    assert granted_at is not None
    updated = AppAccessService.set_access(
        regular_user.id, AE, STATUS_ACTIVE, role="admin", acting_user=admin_user
    )
    assert updated.granted_at == granted_at
    assert updated.role == "admin"


# ---------------------------------------------------------------------------
# Self-request semantics
# ---------------------------------------------------------------------------


def test_self_request_is_idempotent_and_notifies_once(app, regular_user):
    with patch.object(AppAccessService, "_dispatch_request_notification") as notify:
        grant, created = AppAccessService.request_access(regular_user, AE, "please")
        assert created is True
        assert grant.status == STATUS_PENDING
        assert notify.call_count == 1

        grant2, created2 = AppAccessService.request_access(regular_user, AE, "again")
        assert created2 is False
        assert grant2.id == grant.id
        assert grant2.request_note == "again"
        # Still one notification: within the cooldown.
        assert notify.call_count == 1

    assert UserAppAccess.query.filter_by(user_id=regular_user.id).count() == 1


def test_self_request_is_a_noop_when_already_active(app, regular_user):
    _grant(regular_user, AE, status=STATUS_ACTIVE)
    with patch.object(AppAccessService, "_dispatch_request_notification") as notify:
        grant, created = AppAccessService.request_access(regular_user, AE)
    assert created is False
    assert grant.status == STATUS_ACTIVE
    notify.assert_not_called()


def test_revoked_access_cannot_be_self_re_requested(app, regular_user):
    _grant(regular_user, AE, status=STATUS_REVOKED)
    with pytest.raises(AppAccessError) as exc:
        AppAccessService.request_access(regular_user, AE)
    assert exc.value.status == 403
    assert exc.value.payload["status"] == STATUS_REVOKED


def test_notification_resumes_after_cooldown(app, regular_user):
    from datetime import timedelta

    from gefapi.utils import utcnow

    with patch.object(AppAccessService, "_dispatch_request_notification") as notify:
        grant, _ = AppAccessService.request_access(regular_user, AE)
        grant.notified_at = utcnow() - timedelta(hours=25)
        db.session.commit()
        AppAccessService.request_access(regular_user, AE)
        assert notify.call_count == 2


def test_superadmin_recipients_are_deduplicated(app, superadmin_user):
    recipients = AppAccessService.superadmin_recipients()
    lowered = [item.lower() for item in recipients]
    assert len(lowered) == len(set(lowered))
    assert superadmin_user.email in recipients


def test_request_recipients_default_to_superadmins(app, superadmin_user, monkeypatch):
    monkeypatch.delenv("RIO_COHERENCE_ACCESS_REQUEST_EMAILS", raising=False)
    assert AppAccessService.request_notification_recipients(RIO) == (
        AppAccessService.superadmin_recipients()
    )


def test_request_recipients_env_replaces_superadmins(app, superadmin_user, monkeypatch):
    monkeypatch.delenv("AVOIDED_EMISSIONS_ACCESS_REQUEST_EMAILS", raising=False)
    monkeypatch.setenv(
        "RIO_COHERENCE_ACCESS_REQUEST_EMAILS",
        " a@example.org, A@example.org ,,b@example.org",
    )
    recipients = AppAccessService.request_notification_recipients(RIO)
    assert recipients == ["a@example.org", "b@example.org"]
    assert superadmin_user.email not in recipients
    # Other applications are unaffected.
    assert superadmin_user.email in AppAccessService.request_notification_recipients(AE)


def test_avoided_emissions_request_recipients_env(app, superadmin_user, monkeypatch):
    monkeypatch.delenv("RIO_COHERENCE_ACCESS_REQUEST_EMAILS", raising=False)
    monkeypatch.setenv("AVOIDED_EMISSIONS_ACCESS_REQUEST_EMAILS", "ae@example.org")
    assert AppAccessService.request_notification_recipients(AE) == ["ae@example.org"]
    assert superadmin_user.email in AppAccessService.request_notification_recipients(
        RIO
    )


def test_notification_failure_does_not_fail_the_request(app, regular_user):
    with patch(
        "gefapi.tasks.app_access_notifications."
        "notify_superadmins_of_app_access_request.delay",
        side_effect=RuntimeError("broker down"),
    ):
        grant, created = AppAccessService.request_access(regular_user, AE)
    assert created is True
    assert grant.status == STATUS_PENDING


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


def test_revocation_kills_oidc_tokens_but_not_legacy_sessions(
    app, regular_user, admin_user
):
    gated = _client("ae-web", "avoided-emissions", required_app_key=AE)
    ungated = _client("te-qgis", "trends-earth-qgis")
    _grant(regular_user, AE, status=STATUS_ACTIVE)

    create_oidc_refresh_token(regular_user, gated, "openid")
    create_oidc_refresh_token(regular_user, ungated, "openid")
    legacy = RefreshTokenService.create_refresh_token(regular_user.id)

    AppAccessService.set_access(
        regular_user.id, AE, STATUS_REVOKED, acting_user=admin_user
    )

    gated_token = OIDCRefreshToken.query.filter_by(client_id="ae-web").one()
    ungated_token = OIDCRefreshToken.query.filter_by(client_id="te-qgis").one()
    assert gated_token.is_revoked is True
    assert ungated_token.is_revoked is False

    stored_legacy = RefreshToken.query.filter_by(id=legacy.id).one()
    assert stored_legacy.is_revoked is False


# ---------------------------------------------------------------------------
# Token claims
# ---------------------------------------------------------------------------


def test_access_token_carries_client_identity_and_app_access(app, regular_user):
    client = _client("rio-app", "rio-coherence", required_app_key=RIO)
    _grant(regular_user, RIO, status=STATUS_ACTIVE, role="admin")

    from gefapi.services.oidc_service import decode_token

    raw = issue_token(regular_user, client, "access_token", 300, "openid email")
    claims = decode_token(raw, "access_token", audience=client.audience)

    assert claims["azp"] == "rio-app"
    assert claims["client_id"] == "rio-app"
    assert claims["role"] == regular_user.role
    assert claims["app_access"] == [RIO]
    assert claims["app_roles"] == {RIO: "admin"}


def test_id_token_does_not_leak_app_access(app, regular_user):
    client = _client("rio-app2", "rio-coherence-2", required_app_key=RIO)
    _grant(regular_user, RIO, status=STATUS_ACTIVE)

    from gefapi.services.oidc_service import decode_token

    raw = issue_token(regular_user, client, "id_token", 300, "openid email")
    claims = decode_token(raw, "id_token", audience=client.client_id)
    assert "app_access" not in claims


# ---------------------------------------------------------------------------
# Loopback redirect matching (RFC 8252)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("registered", "candidate", "expected"),
    [
        ("http://127.0.0.1/callback", "http://127.0.0.1:51234/callback", True),
        ("http://127.0.0.1/callback", "http://127.0.0.1/callback", True),
        ("http://[::1]/callback", "http://[::1]:8080/callback", True),
        # Only literal loopback hosts qualify - never localhost by name.
        ("http://127.0.0.1/callback", "http://localhost:51234/callback", False),
        ("http://127.0.0.1/callback", "http://evil.test:51234/callback", False),
        # Scheme and path must still match exactly.
        ("http://127.0.0.1/callback", "https://127.0.0.1:51234/callback", False),
        ("http://127.0.0.1/callback", "http://127.0.0.1:51234/other", False),
        ("https://app.test/cb", "https://app.test:8443/cb", False),
    ],
)
def test_loopback_redirect_matching(app, registered, candidate, expected):
    client = OAuthClient(
        name="native",
        client_id="native",
        redirect_uris=registered,
        audience="native-audience",
        scopes="openid",
        is_public=True,
    )
    assert client.matches_redirect_uri(candidate) is expected


def test_empty_redirect_uri_is_rejected(app):
    client = OAuthClient(
        name="native2",
        client_id="native2",
        redirect_uris="http://127.0.0.1/callback",
        audience="native-audience-2",
        scopes="openid",
        is_public=True,
    )
    assert client.matches_redirect_uri("") is False
    assert client.matches_redirect_uri(None) is False


# ---------------------------------------------------------------------------
# OIDC gate at /oauth/authorize
# ---------------------------------------------------------------------------


def _authorize(client_app, oauth_client, user=None):
    challenge = "a" * 43
    return client_app.post(
        "/oauth/authorize",
        data={
            "client_id": oauth_client.client_id,
            "redirect_uri": oauth_client.allowed_redirect_uris()[0],
            "scope": "openid email",
            "state": "state-123",
            "nonce": "nonce-123",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "login_csrf": "csrf",
            "email": user.email,
            "password": USER_TEST_PASSWORD,
        },
        follow_redirects=False,
    )


def test_gated_authorize_denies_and_records_request(app, client, regular_user):
    oauth_client = _client(
        "ae-gate",
        "ae-gate-audience",
        required_app_key=AE,
        redirect="https://ae.test/cb",
    )
    _verified(regular_user)
    with client.session_transaction() as session:
        session["oidc_login_csrf"] = "csrf"
        session["oidc_user_id"] = str(regular_user.id)

    with patch.object(AppAccessService, "_dispatch_request_notification") as notify:
        response = _authorize(client, oauth_client, user=regular_user)

    assert response.status_code == 302
    parsed = urllib.parse.urlparse(response.headers["Location"])
    params = urllib.parse.parse_qs(parsed.query)
    assert params["error"] == ["access_denied"]
    assert params["state"] == ["state-123"]
    assert params["te_app_access"] == ["pending"]
    assert params["te_app_key"] == [AE]
    assert "code" not in params

    grant = UserAppAccess.query.filter_by(user_id=regular_user.id, app_key=AE).one()
    assert grant.status == STATUS_PENDING
    assert notify.call_count == 1


def test_gated_authorize_reports_revoked_without_reopening(app, client, regular_user):
    oauth_client = _client(
        "ae-gate2",
        "ae-gate-audience-2",
        required_app_key=AE,
        redirect="https://ae.test/cb",
    )
    _grant(regular_user, AE, status=STATUS_REVOKED)
    _verified(regular_user)
    with client.session_transaction() as session:
        session["oidc_login_csrf"] = "csrf"
        session["oidc_user_id"] = str(regular_user.id)

    response = _authorize(client, oauth_client, user=regular_user)

    params = urllib.parse.parse_qs(
        urllib.parse.urlparse(response.headers["Location"]).query
    )
    assert params["te_app_access"] == ["revoked"]
    grant = UserAppAccess.query.filter_by(user_id=regular_user.id, app_key=AE).one()
    assert grant.status == STATUS_REVOKED


def test_ungated_authorize_issues_a_code(app, client, regular_user):
    oauth_client = _client("ui-gate", "ui-gate-audience", redirect="https://ui.test/cb")
    _verified(regular_user)
    with client.session_transaction() as session:
        session["oidc_login_csrf"] = "csrf"
        session["oidc_user_id"] = str(regular_user.id)

    response = _authorize(client, oauth_client, user=regular_user)

    assert response.status_code == 302, response.get_data(as_text=True)[:300]
    params = urllib.parse.parse_qs(
        urllib.parse.urlparse(response.headers["Location"]).query
    )
    assert "code" in params
    assert "error" not in params


def test_authorize_rejects_browser_session_from_before_password_change(
    app, client, regular_user
):
    oauth_client = _client(
        "reauth-client", "reauth-audience", redirect="https://reauth.test/cb"
    )
    _verified(regular_user)
    user = db.session.get(User, regular_user.id)
    previous_auth_version = user.auth_version
    user.auth_version += 1
    db.session.commit()

    with client.session_transaction() as session:
        session["oidc_user_id"] = str(regular_user.id)
        session["oidc_auth_version"] = previous_auth_version

    response = client.get(
        "/oauth/authorize",
        query_string={
            "client_id": oauth_client.client_id,
            "redirect_uri": "https://reauth.test/cb",
            "response_type": "code",
            "scope": "openid",
            "state": "state-123",
            "nonce": "nonce-123",
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
        },
    )

    assert response.status_code == 200
    assert b'name="login_csrf"' in response.data
    assert AuthorizationCode.query.count() == 0
    with client.session_transaction() as session:
        assert "oidc_user_id" not in session
        assert "oidc_auth_version" not in session


def test_denial_redirect_requires_a_validated_redirect_uri(app, client, regular_user):
    oauth_client = _client(
        "ae-gate3",
        "ae-gate-audience-3",
        required_app_key=AE,
        redirect="https://ae.test/cb",
    )
    with client.session_transaction() as session:
        session["oidc_login_csrf"] = "csrf"
        session["oidc_user_id"] = str(regular_user.id)

    response = client.post(
        "/oauth/authorize",
        data={
            "client_id": oauth_client.client_id,
            "redirect_uri": "https://attacker.test/cb",
            "scope": "openid",
            "state": "s",
            "nonce": "n",
            "response_type": "code",
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
        },
    )
    # Rejected before any redirect is issued.
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


def test_refresh_grant_is_denied_after_revocation(app, client, regular_user):
    oauth_client = _client("ae-refresh", "ae-refresh-audience", required_app_key=AE)
    _grant(regular_user, AE, status=STATUS_ACTIVE)
    refresh = create_oidc_refresh_token(regular_user, oauth_client, "openid")

    AppAccessService.set_access(regular_user.id, AE, STATUS_REVOKED)

    response = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": oauth_client.client_id,
            "refresh_token": refresh,
        },
    )
    assert response.status_code in (400, 403)
    assert response.get_json()["error"] in ("access_denied", "invalid_grant")


# ---------------------------------------------------------------------------
# Admin and self-service routes
# ---------------------------------------------------------------------------


def test_admin_can_grant_and_revoke(app, client, auth_headers_admin, regular_user):
    response = client.post(
        f"/api/v1/admin/users/{regular_user.id}/app-access",
        json={"app_key": AE, "status": "active", "role": "admin"},
        headers=auth_headers_admin,
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["status"] == STATUS_ACTIVE
    assert response.get_json()["data"]["role"] == "admin"

    response = client.delete(
        f"/api/v1/admin/users/{regular_user.id}/app-access/{AE}",
        headers=auth_headers_admin,
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["status"] == STATUS_REVOKED


def test_non_admin_cannot_grant(app, client, auth_headers_user, regular_user):
    response = client.post(
        f"/api/v1/admin/users/{regular_user.id}/app-access",
        json={"app_key": AE, "status": "active"},
        headers=auth_headers_user,
    )
    assert response.status_code == 403


def test_admin_list_filters_by_status(app, client, auth_headers_admin, regular_user):
    _grant(regular_user, AE, status=STATUS_PENDING)
    response = client.get(
        "/api/v1/admin/app-access?status=pending&include=user",
        headers=auth_headers_admin,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["total"] == 1
    assert body["data"][0]["app_key"] == AE
    assert body["data"][0]["user"]["email"] == regular_user.email


def test_user_can_view_and_request_own_access(app, client, auth_headers_user):
    response = client.get("/api/v1/user/me/app-access", headers=auth_headers_user)
    assert response.status_code == 200
    assert {item["app_key"] for item in response.get_json()["data"]} == {AE, RIO}

    with patch.object(AppAccessService, "_dispatch_request_notification"):
        response = client.post(
            f"/api/v1/user/me/app-access/{AE}",
            json={"request_note": "for the pilot"},
            headers=auth_headers_user,
        )
    assert response.status_code == 201
    assert response.get_json()["data"]["status"] == STATUS_PENDING

    with patch.object(AppAccessService, "_dispatch_request_notification"):
        response = client.post(
            f"/api/v1/user/me/app-access/{AE}", headers=auth_headers_user
        )
    assert response.status_code == 200
    assert response.get_json()["created"] is False


def test_unknown_app_key_returns_404(app, client, auth_headers_user):
    response = client.post(
        "/api/v1/user/me/app-access/not-an-app", headers=auth_headers_user
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Regression: legacy clients are untouched
# ---------------------------------------------------------------------------


def test_legacy_login_and_refresh_still_work_without_grants(app, client, regular_user):
    response = client.post(
        "/auth",
        json={"email": regular_user.email, "password": USER_TEST_PASSWORD},
    )
    assert response.status_code == 200
    body = response.get_json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}

    me = client.get("/api/v1/user/me", headers=headers)
    assert me.status_code == 200
    # The default payload shape is unchanged.
    assert "app_access" not in me.get_json()["data"]

    refreshed = client.post(
        "/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert refreshed.status_code == 200


def test_plugin_requests_work_with_client_header(app, client, auth_headers_user):
    headers = dict(auth_headers_user)
    headers["X-TE-Client"] = "qgis_plugin/2.2.4 (Windows; QGIS 3.34.0)"
    response = client.get("/api/v1/user/me", headers=headers)
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("token", "expected_error"),
    [
        ("not-a-token", "invalid_token"),
        ("", "authorization_required"),
    ],
)
def test_auth_failure_bodies_are_unchanged(app, client, token, expected_error):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.get("/api/v1/user/me", headers=headers)
    assert response.status_code == 401
    body = response.get_json()
    assert body["error"] == expected_error
    assert body["status"] == 401


def test_app_access_include_is_opt_in(app, client, auth_headers_admin, regular_user):
    _grant(regular_user, AE, status=STATUS_ACTIVE)
    default = client.get(f"/api/v1/user/{regular_user.id}", headers=auth_headers_admin)
    assert "app_access" not in default.get_json()["data"]

    included = client.get(
        f"/api/v1/user/{regular_user.id}?include=app_access",
        headers=auth_headers_admin,
    )
    assert included.get_json()["data"]["app_access"][0]["app_key"] == AE


def test_unknown_include_values_are_ignored(app, client, auth_headers_user):
    response = client.get(
        "/api/v1/user/me?include=definitely_not_a_field", headers=auth_headers_user
    )
    assert response.status_code == 200
