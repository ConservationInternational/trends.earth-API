"""Service-client tokens must not escape their scopes via user/client APIs.

Covers two audit findings:

* ``user:write``-only service tokens owned by privileged users must not
  promote/edit/delete other users or reset passwords (``admin:write`` is also
  required), and no service token may reset its owner's password without the
  old password.
* No service-client token may mint new persistent service clients.

Legacy user JWTs (used by the API UI) and the plugin's self-service password
change keep working with the exact payloads those clients send.
"""

from flask_jwt_extended import create_access_token
import pytest

from gefapi import db
from gefapi.models import User
from gefapi.models.service_client import ServiceClient
from gefapi.services.oauth2_service import OAuth2Service
from tests.conftest import (
    ADMIN_TEST_PASSWORD,
    SUPERADMIN_TEST_PASSWORD,
    USER_TEST_PASSWORD,
)

UI_NEW_PASSWORD = "UiChanged-Pass123!"


def _service_headers(user, scopes):
    """Headers carrying the same claims ``POST /oauth/token`` issues."""
    user = db.session.merge(user)
    token = create_access_token(
        identity=user.id,
        additional_claims={
            "grant_type": "client_credentials",
            "scopes": scopes,
            "auth_version": user.auth_version,
        },
    )
    return {"Authorization": f"Bearer {token}"}


def _user_headers(user):
    user = db.session.merge(user)
    token = create_access_token(
        identity=user.id, additional_claims={"auth_version": user.auth_version}
    )
    return {"Authorization": f"Bearer {token}"}


def _reload(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _client_count(user):
    db.session.expire_all()
    return ServiceClient.query.filter_by(user_id=user.id, revoked=False).count()


# ---------------------------------------------------------------------------
# Finding 1: privileged user mutation requires admin:write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scopes", ["user:write", "user:read user:write"])
def test_user_write_superadmin_token_cannot_promote_user(
    app, client, superadmin_user, regular_user, scopes
):
    with app.app_context():
        response = client.patch(
            f"/api/v1/user/{regular_user.id}",
            json={"role": "SUPERADMIN"},
            headers=_service_headers(superadmin_user, scopes),
        )
        assert response.status_code == 403
        assert "admin:write" in response.get_json()["detail"]
        assert _reload(regular_user.id).role == "USER"


def test_user_write_superadmin_token_cannot_edit_or_deactivate_other_user(
    app, client, superadmin_user, regular_user
):
    with app.app_context():
        response = client.patch(
            f"/api/v1/user/{regular_user.id}",
            json={"name": "Hijacked", "is_active": False},
            headers=_service_headers(superadmin_user, "user:write"),
        )
        assert response.status_code == 403
        reloaded = _reload(regular_user.id)
        assert reloaded.name == "Regular User"
        assert reloaded.is_active is True


def test_user_write_superadmin_token_cannot_promote_self(app, client, admin_user):
    with app.app_context():
        admin = db.session.merge(admin_user)
        admin.role = "SUPERADMIN"
        db.session.commit()
        response = client.patch(
            f"/api/v1/user/{admin.id}",
            json={"role": "SUPERADMIN", "name": "x"},
            headers=_service_headers(admin, "user:write"),
        )
        assert response.status_code == 403


def test_user_write_superadmin_token_cannot_delete_user(
    app, client, superadmin_user, regular_user
):
    with app.app_context():
        response = client.delete(
            f"/api/v1/user/{regular_user.id}",
            headers=_service_headers(superadmin_user, "user:write"),
        )
        assert response.status_code == 403
        assert _reload(regular_user.id) is not None


@pytest.mark.parametrize(
    "owner_fixture", ["superadmin_user", "admin_user"], ids=["superadmin", "admin"]
)
def test_user_write_admin_token_cannot_reset_other_password(
    app, client, request, regular_user, owner_fixture
):
    owner = request.getfixturevalue(owner_fixture)
    with app.app_context():
        response = client.patch(
            f"/api/v1/user/{regular_user.id}/change-password",
            json={"new_password": UI_NEW_PASSWORD},
            headers=_service_headers(owner, "user:write"),
        )
        assert response.status_code == 403
        reloaded = _reload(regular_user.id)
        assert reloaded.check_password(USER_TEST_PASSWORD)
        assert not reloaded.check_password(UI_NEW_PASSWORD)


def test_admin_write_without_user_write_is_still_denied(
    app, client, superadmin_user, regular_user
):
    with app.app_context():
        response = client.patch(
            f"/api/v1/user/{regular_user.id}",
            json={"role": "ADMIN"},
            headers=_service_headers(superadmin_user, "admin:write"),
        )
        assert response.status_code == 403
        assert "user:write" in response.get_json()["detail"]
        assert _reload(regular_user.id).role == "USER"


def test_admin_write_superadmin_token_can_manage_other_users(
    app, client, superadmin_user, regular_user
):
    with app.app_context():
        headers = _service_headers(superadmin_user, "user:write admin:write")
        promote = client.patch(
            f"/api/v1/user/{regular_user.id}",
            json={"role": "ADMIN"},
            headers=headers,
        )
        assert promote.status_code == 200, promote.get_json()
        assert _reload(regular_user.id).role == "ADMIN"

        reset = client.patch(
            f"/api/v1/user/{regular_user.id}/change-password",
            json={"new_password": UI_NEW_PASSWORD},
            headers=headers,
        )
        assert reset.status_code == 200, reset.get_json()
        assert _reload(regular_user.id).check_password(UI_NEW_PASSWORD)

        delete = client.delete(f"/api/v1/user/{regular_user.id}", headers=headers)
        assert delete.status_code == 200, delete.get_json()
        assert _reload(regular_user.id) is None


@pytest.mark.parametrize("scopes", ["", "all", "user:write admin:write"])
def test_full_or_admin_write_token_still_bound_by_role_checks(
    app, client, admin_user, regular_user, scopes
):
    """ADMIN owners stay unable to change roles even with admin:write."""
    with app.app_context():
        response = client.patch(
            f"/api/v1/user/{regular_user.id}",
            json={"role": "SUPERADMIN"},
            headers=_service_headers(admin_user, scopes),
        )
        assert response.status_code == 403
        assert _reload(regular_user.id).role == "USER"


def test_admin_write_admin_token_cannot_reset_superadmin_password(
    app, client, admin_user, superadmin_user
):
    with app.app_context():
        response = client.patch(
            f"/api/v1/user/{superadmin_user.id}/change-password",
            json={"new_password": UI_NEW_PASSWORD},
            headers=_service_headers(admin_user, "user:write admin:write"),
        )
        assert response.status_code == 403
        assert _reload(superadmin_user.id).check_password(SUPERADMIN_TEST_PASSWORD)


@pytest.mark.parametrize("scopes", ["", "all", "user:write admin:write"])
def test_service_token_cannot_reset_owner_password_without_old_password(
    app, client, superadmin_user, scopes
):
    with app.app_context():
        response = client.patch(
            f"/api/v1/user/{superadmin_user.id}/change-password",
            json={"new_password": UI_NEW_PASSWORD},
            headers=_service_headers(superadmin_user, scopes),
        )
        assert response.status_code == 403
        assert "/user/me/change-password" in response.get_json()["detail"]
        reloaded = _reload(superadmin_user.id)
        assert reloaded.check_password(SUPERADMIN_TEST_PASSWORD)


def test_service_token_cannot_reset_owner_password_by_email(
    app, client, superadmin_user
):
    with app.app_context():
        response = client.patch(
            f"/api/v1/user/{superadmin_user.email}/change-password",
            json={"new_password": UI_NEW_PASSWORD},
            headers=_service_headers(superadmin_user, "user:write admin:write"),
        )
        assert response.status_code == 403
        assert _reload(superadmin_user.id).check_password(SUPERADMIN_TEST_PASSWORD)


def test_user_write_token_cannot_create_privileged_user(app, client, superadmin_user):
    with app.app_context():
        response = client.post(
            "/api/v1/user",
            json={
                "email": "promoted-by-token@test.com",
                "name": "Promoted",
                "role": "SUPERADMIN",
                "password": UI_NEW_PASSWORD,
                "repeatPassword": UI_NEW_PASSWORD,
            },
            headers=_service_headers(superadmin_user, "user:write"),
        )
        assert response.status_code == 403
        assert User.query.filter_by(email="promoted-by-token@test.com").count() == 0


# --- Plugin self-service flows keep working with user:write ---------------


def test_user_write_token_self_password_change_requires_old_password(
    app, client, regular_user
):
    with app.app_context():
        headers = _service_headers(regular_user, "user:write")
        wrong = client.patch(
            "/api/v1/user/me/change-password",
            json={"old_password": "WrongPass123!", "new_password": UI_NEW_PASSWORD},
            headers=headers,
        )
        assert wrong.status_code == 401

        ok = client.patch(
            "/api/v1/user/me/change-password",
            json={"old_password": USER_TEST_PASSWORD, "new_password": UI_NEW_PASSWORD},
            headers=headers,
        )
        assert ok.status_code == 200, ok.get_json()
        assert _reload(regular_user.id).check_password(UI_NEW_PASSWORD)


def test_user_write_token_can_update_own_profile(app, client, superadmin_user):
    with app.app_context():
        response = client.patch(
            "/api/v1/user/me",
            json={"name": "Renamed", "role": "USER"},
            headers=_service_headers(superadmin_user, "user:write"),
        )
        assert response.status_code == 200
        reloaded = _reload(superadmin_user.id)
        assert reloaded.name == "Renamed"
        assert reloaded.role == "SUPERADMIN"


# --- API UI (legacy user JWT) exact payloads ---------------------------------


def test_ui_superadmin_save_user_edits_payload(
    app, client, superadmin_user, regular_user
):
    """Mirrors trendsearth_ui/callbacks/edit.py::save_user_edits."""
    with app.app_context():
        payload = {
            "name": "Edited Name",
            "email": "user@test.com",
            "institution": "Edited Institution",
            "country": "Kenya",
            "role": "ADMIN",
            "max_concurrent_executions": None,
        }
        response = client.patch(
            f"/api/v1/user/{regular_user.id}",
            json=payload,
            headers=_user_headers(superadmin_user),
        )
        assert response.status_code == 200, response.get_json()
        reloaded = _reload(regular_user.id)
        assert reloaded.role == "ADMIN"
        assert reloaded.name == "Edited Name"

        payload["max_concurrent_executions"] = 5
        response = client.patch(
            f"/api/v1/user/{regular_user.id}",
            json=payload,
            headers=_user_headers(superadmin_user),
        )
        assert response.status_code == 200, response.get_json()


@pytest.mark.parametrize(
    "owner_fixture", ["superadmin_user", "admin_user"], ids=["superadmin", "admin"]
)
def test_ui_admin_change_password_payload(
    app, client, request, regular_user, owner_fixture
):
    """Mirrors trendsearth_ui/callbacks/edit.py::admin_change_password."""
    owner = request.getfixturevalue(owner_fixture)
    with app.app_context():
        response = client.patch(
            f"/api/v1/user/{regular_user.id}/change-password",
            json={"new_password": UI_NEW_PASSWORD},
            headers=_user_headers(owner),
        )
        assert response.status_code == 200, response.get_json()
        assert _reload(regular_user.id).check_password(UI_NEW_PASSWORD)


def test_ui_admin_change_password_on_own_row_still_allowed(app, client, admin_user):
    """Legacy UI sessions keep their existing admin-reset behaviour."""
    with app.app_context():
        response = client.patch(
            f"/api/v1/user/{admin_user.id}/change-password",
            json={"new_password": UI_NEW_PASSWORD},
            headers=_user_headers(admin_user),
        )
        assert response.status_code == 200, response.get_json()
        assert _reload(admin_user.id).check_password(UI_NEW_PASSWORD)
        assert not _reload(admin_user.id).check_password(ADMIN_TEST_PASSWORD)


def test_ui_superadmin_delete_user(app, client, superadmin_user, regular_user):
    with app.app_context():
        response = client.delete(
            f"/api/v1/user/{regular_user.id}", headers=_user_headers(superadmin_user)
        )
        assert response.status_code == 200, response.get_json()
        assert _reload(regular_user.id) is None


def test_ui_profile_and_password_payloads(app, client, regular_user):
    """Mirrors trendsearth_ui/callbacks/profile.py."""
    with app.app_context():
        headers = _user_headers(regular_user)
        prefs = client.patch(
            "/api/v1/user/me",
            json={
                "email_notifications_enabled": False,
                "email_subscription_news": True,
                "email_subscription_engagement": False,
                "email_subscription_system_updates": True,
            },
            headers=headers,
        )
        assert prefs.status_code == 200, prefs.get_json()

        change = client.patch(
            "/api/v1/user/me/change-password",
            json={"old_password": USER_TEST_PASSWORD, "new_password": UI_NEW_PASSWORD},
            headers=headers,
        )
        assert change.status_code == 200, change.get_json()
        assert _reload(regular_user.id).check_password(UI_NEW_PASSWORD)


# ---------------------------------------------------------------------------
# Finding 2: service tokens cannot mint service clients
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token_scopes", ["client:manage", "client:manage admin:write", "all", ""]
)
@pytest.mark.parametrize(
    "requested_scopes", ["", "all", "user:write admin:write", "execution:read"]
)
def test_service_token_cannot_create_service_client(
    app, client, admin_user, token_scopes, requested_scopes
):
    with app.app_context():
        before = _client_count(admin_user)
        response = client.post(
            "/api/v1/oauth/clients",
            json={"name": "minted", "scopes": requested_scopes},
            headers=_service_headers(admin_user, token_scopes),
        )
        assert response.status_code == 403
        assert "client_secret" not in str(response.get_json())
        assert _client_count(admin_user) == before


def test_exchanged_service_token_cannot_create_service_client(app, client, admin_user):
    """End-to-end: credentials -> /oauth/token -> POST /oauth/clients."""
    with app.app_context():
        owner = db.session.merge(admin_user)
        raw_secret, svc = OAuth2Service.create_client(
            user=owner, name="manager", scopes="client:manage"
        )
        token_resp = client.post(
            "/api/v1/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": svc.client_id,
                "client_secret": raw_secret,
            },
        )
        assert token_resp.status_code == 200
        headers = {"Authorization": f"Bearer {token_resp.get_json()['access_token']}"}
        before = _client_count(admin_user)

        created = client.post(
            "/api/v1/oauth/clients",
            json={"name": "escape", "scopes": "all"},
            headers=headers,
        )
        assert created.status_code == 403
        assert _client_count(admin_user) == before

        listed = client.get("/api/v1/oauth/clients", headers=headers)
        assert listed.status_code == 200
        assert [c["client_id"] for c in listed.get_json()["data"]] == [svc.client_id]


def test_client_manage_token_can_still_list_and_revoke(app, client, admin_user):
    with app.app_context():
        owner = db.session.merge(admin_user)
        _, target = OAuth2Service.create_client(user=owner, name="old")
        headers = _service_headers(admin_user, "client:manage")

        listed = client.get("/api/v1/oauth/clients", headers=headers)
        assert listed.status_code == 200
        assert target.client_id in [c["client_id"] for c in listed.get_json()["data"]]

        revoked = client.delete(f"/api/v1/oauth/clients/{target.id}", headers=headers)
        assert revoked.status_code == 200
        assert revoked.get_json()["data"]["revoked"] is True


@pytest.mark.parametrize(
    ("owner_fixture", "scopes"),
    [
        ("regular_user", "all"),
        ("regular_user", "execution:read execution:write"),
        ("admin_user", "user:write admin:write"),
    ],
)
def test_ui_create_service_client_payload(app, client, request, owner_fixture, scopes):
    """Mirrors trendsearth_ui/callbacks/service_credentials.py (legacy JWT)."""
    owner = request.getfixturevalue(owner_fixture)
    with app.app_context():
        response = client.post(
            "/api/v1/oauth/clients",
            json={"name": "My script", "scopes": scopes, "expires_in_days": 90},
            headers=_user_headers(owner),
        )
        assert response.status_code == 201, response.get_json()
        data = response.get_json()["data"]
        assert data["client_secret"]
        assert data["scopes"] == scopes
