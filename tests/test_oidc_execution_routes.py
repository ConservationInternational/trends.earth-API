"""OIDC access tokens on the script/execution routes.

Avoided Emissions runs the analysis script on behalf of the signed-in user
with that user's own OIDC access token.  These tests pin two guarantees:

* Legacy HS256 tokens (QGIS plugin, API UI, service clients) see exactly the
  same responses on the converted routes as on routes that still use
  ``jwt_required``.
* OIDC tokens are accepted only with the matching execution scope, from a
  client registered for it, for a user whose application grant is still live.
"""

from datetime import timedelta
import json
from unittest.mock import patch

from flask_jwt_extended import create_access_token
import pytest

from gefapi import db
from gefapi.models import Execution, OAuthClient, OIDCRefreshToken, Script, User
from gefapi.models.app_access import STATUS_ACTIVE, STATUS_REVOKED, UserAppAccess
from gefapi.services.oidc_service import issue_token
from gefapi.services.user_service import UserService
from tests.conftest import USER_TEST_PASSWORD

AE = "avoided_emissions"
RIO = "rio_coherence"
BASE = "openid email profile"
APP = "app:read account:write app-users:read app-access:write"
EXEC = "script:read execution:read execution:write"
AE_SCOPES = f"{BASE} {APP} {EXEC}"


@pytest.fixture(autouse=True)
def _no_rate_limits(rate_limiting_disabled):
    yield


def _oauth_client(client_id, app_key, scopes):
    client = OAuthClient(
        name=client_id,
        client_id=client_id,
        redirect_uris="https://app.test/cb",
        post_logout_redirect_uris="",
        audience=f"{client_id}-aud",
        scopes=scopes,
        is_public=False,
        required_app_key=app_key,
    )
    db.session.add(client)
    db.session.commit()
    return client


def _grant(user, app_key, status=STATUS_ACTIVE):
    db.session.add(
        UserAppAccess(user_id=user.id, app_key=app_key, status=status, role="member")
    )
    db.session.commit()


def _user(email="ae-user@app.test"):
    user = User(
        email=email,
        password=USER_TEST_PASSWORD,
        name="AE User",
        role="USER",
        country="US",
        institution="Inst",
    )
    user.email_verified = True
    db.session.add(user)
    db.session.commit()
    return user


def _oidc(user, client, scope=AE_SCOPES):
    token = issue_token(user, client, "access_token", 300, scope)
    return {"Authorization": f"Bearer {token}"}


def _legacy(user, **kwargs):
    return {
        "Authorization": f"Bearer {create_access_token(identity=user.id, **kwargs)}"
    }


@pytest.fixture
def ae(app):
    client = _oauth_client("avoided-emissions-web", AE, AE_SCOPES)
    user = _user()
    _grant(user, AE)
    return client, user


def _run(client, script, headers):
    script_id = db.session.merge(script).id
    with patch("gefapi.services.execution_service._dispatch_execution"):
        return client.post(
            f"/api/v1/script/{script_id}/run", json={"a": 1}, headers=headers
        )


# ---------------------------------------------------------------------------
# Legacy contract: converted routes answer exactly like jwt_required routes
# ---------------------------------------------------------------------------

CONVERTED_GET = ["/api/v1/execution/user", "/api/v1/execution"]
REFERENCE_GET = "/api/v1/user/me"


def _same(client, headers):
    reference = client.get(REFERENCE_GET, headers=headers)
    for url in CONVERTED_GET:
        response = client.get(url, headers=headers)
        assert response.status_code == reference.status_code, url
        assert response.get_json() == reference.get_json(), url
    return reference.status_code


def test_legacy_missing_token_contract(app, client):
    assert _same(client, {}) == 401


@pytest.mark.parametrize(
    "token",
    ["not-a-jwt", "a.b.c", "Bearer", "eyJhbGciOiJSUzI1NiJ9.e30.sig"],
)
def test_legacy_invalid_token_contract(app, client, token):
    assert _same(client, {"Authorization": f"Bearer {token}"}) in (401, 422)


def test_legacy_expired_token_contract(app, client, regular_user):
    headers = _legacy(regular_user, expires_delta=timedelta(seconds=-5))
    assert _same(client, headers) == 401


def test_legacy_inactive_user_contract(app, client, regular_user):
    headers = _legacy(regular_user)
    User.query.filter_by(id=regular_user.id).update({"is_active": False})
    db.session.commit()
    assert _same(client, headers) == 401


def test_legacy_auth_version_mismatch_contract(app, client, regular_user):
    headers = _legacy(regular_user)
    User.query.filter_by(id=regular_user.id).update(
        {"auth_version": User.auth_version + 1}
    )
    db.session.commit()
    assert _same(client, headers) == 401


def test_legacy_token_runs_and_lists(app, client, regular_user, sample_script):
    sample_script = db.session.merge(sample_script)
    headers = _legacy(regular_user)
    response = _run(client, sample_script, headers)
    assert response.status_code == 200
    assert response.get_json()["data"]["user_id"] == str(regular_user.id)
    assert client.get("/api/v1/execution/user", headers=headers).status_code == 200
    assert (
        client.get(f"/api/v1/script/{sample_script.id}", headers=headers).status_code
        == 200
    )


# ---------------------------------------------------------------------------
# OIDC access
# ---------------------------------------------------------------------------


def test_oidc_token_runs_script_as_the_user(app, client, ae, sample_script):
    sample_script = db.session.merge(sample_script)
    oauth, user = ae
    headers = _oidc(user, oauth)
    response = _run(client, sample_script, headers)
    assert response.status_code == 200, response.get_json()
    execution_id = response.get_json()["data"]["id"]
    assert str(db.session.get(Execution, execution_id).user_id) == str(user.id)

    listed = client.get("/api/v1/execution/user", headers=headers)
    assert listed.status_code == 200
    assert execution_id in [item["id"] for item in listed.get_json()["data"]]
    assert (
        client.get(f"/api/v1/execution/{execution_id}", headers=headers).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/script/{sample_script.id}", headers=headers).status_code
        == 200
    )


def test_oidc_token_cannot_read_other_users_execution(
    app, client, ae, sample_execution
):
    oauth, user = ae
    response = client.get(
        f"/api/v1/execution/{sample_execution.id}", headers=_oidc(user, oauth)
    )
    assert response.status_code in (403, 404)


def test_oidc_token_without_execution_scope_is_forbidden(
    app, client, ae, sample_script
):
    oauth, user = ae
    headers = _oidc(user, oauth, scope=f"{BASE} {APP}")
    response = _run(client, sample_script, headers)
    assert response.status_code == 403
    assert response.get_json()["detail"] == (
        "Insufficient scope. Required: execution:write"
    )
    assert client.get("/api/v1/execution/user", headers=headers).status_code == 403


def test_client_not_registered_for_execution_is_forbidden(app, client, sample_script):
    # Rio's client is not registered for execution scopes, so even a token
    # whose scope claim lists them is refused.
    oauth = _oauth_client("rio-coherence", RIO, f"{BASE} {APP}")
    user = _user("rio@app.test")
    _grant(user, RIO)
    headers = _oidc(user, oauth, scope=AE_SCOPES)
    assert _run(client, sample_script, headers).status_code == 403
    assert client.get("/api/v1/execution/user", headers=headers).status_code == 403


def test_revoked_grant_takes_effect_before_token_expiry(app, client, ae, sample_script):
    oauth, user = ae
    headers = _oidc(user, oauth)
    UserAppAccess.query.filter_by(user_id=user.id, app_key=AE).update(
        {"status": STATUS_REVOKED}
    )
    db.session.commit()
    response = _run(client, sample_script, headers)
    assert response.status_code == 403
    assert response.get_json()["error"] == "app_access_required"


def test_oidc_auth_version_mismatch_is_rejected(app, client, ae):
    oauth, user = ae
    headers = _oidc(user, oauth)
    User.query.filter_by(id=user.id).update({"auth_version": User.auth_version + 1})
    db.session.commit()
    assert client.get("/api/v1/execution/user", headers=headers).status_code == 401


def test_rate_limit_key_uses_oidc_user(app, ae):
    from gefapi.utils.rate_limiting import get_user_id_or_ip

    oauth, user = ae
    with app.test_request_context(headers=_oidc(user, oauth)):
        assert get_user_id_or_ip() == f"user:{user.id}"


def test_rate_limit_key_uses_legacy_user(app, regular_user):
    from gefapi.utils.rate_limiting import get_user_id_or_ip

    with app.test_request_context(headers=_legacy(regular_user)):
        assert get_user_id_or_ip() == f"user:{regular_user.id}"


# ---------------------------------------------------------------------------
# Session parity: OIDC access tokens die with a password change
# ---------------------------------------------------------------------------


def test_password_change_invalidates_oidc_access_tokens(app, client, ae):
    oauth, user = ae
    headers = _oidc(user, oauth)
    assert client.get("/api/v1/execution/user", headers=headers).status_code == 200
    UserService.admin_change_password(user, "N3w-Secure-Passw0rd!")
    assert client.get("/api/v1/execution/user", headers=headers).status_code == 401
    assert (
        OIDCRefreshToken.query.filter_by(user_id=user.id, is_revoked=False).count() == 0
    )


# ---------------------------------------------------------------------------
# Application-grant script access
# ---------------------------------------------------------------------------


@pytest.fixture
def restricted_script(app, admin_user):
    script = Script(name="AE Analysis", slug="ae-analysis", user_id=admin_user.id)
    script.status = "SUCCESS"
    script.public = False
    script.restricted = True
    script.allowed_roles = json.dumps([])
    script.allowed_users = json.dumps([])
    db.session.add(script)
    db.session.commit()
    return script


def test_required_app_key_grants_script_access(
    app, client, ae, restricted_script, auth_headers_admin
):
    oauth, user = ae
    url = f"/api/v1/script/{restricted_script.id}"
    # Inaccessible scripts are reported as not found.
    assert client.get(url, headers=_oidc(user, oauth)).status_code == 404

    response = client.put(
        f"{url}/access/app", json={"app_key": AE}, headers=auth_headers_admin
    )
    assert response.status_code == 200, response.get_json()
    assert client.get(url, headers=_oidc(user, oauth)).status_code == 200
    assert _run(client, restricted_script, _oidc(user, oauth)).status_code == 200

    # The grant is additive: users without it are still refused.
    other = _user("no-grant@app.test")
    assert client.get(url, headers=_legacy(other)).status_code == 404

    response = client.put(
        f"{url}/access/app", json={"app_key": None}, headers=auth_headers_admin
    )
    assert response.status_code == 200
    assert client.get(url, headers=_oidc(user, oauth)).status_code == 404


def test_set_required_app_rejects_unknown_app_and_non_admins(
    app, client, restricted_script, auth_headers_admin, auth_headers_user
):
    url = f"/api/v1/script/{restricted_script.id}/access/app"
    assert (
        client.put(
            url, json={"app_key": "nope"}, headers=auth_headers_admin
        ).status_code
        == 400
    )
    assert (
        client.put(url, json={"app_key": AE}, headers=auth_headers_user).status_code
        == 403
    )


def test_required_app_key_hidden_from_default_payload(app, restricted_script):
    restricted_script.required_app_key = AE
    db.session.commit()
    assert "required_app_key" not in restricted_script.serialize()
