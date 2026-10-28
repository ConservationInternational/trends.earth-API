"""Per-application access-request notification recipients.

Superadmins maintain one recipient list per application (active ADMIN or
SUPERADMIN accounts only).  An empty list means every superadmin is emailed.
Application administrators who can approve users through the application are
always added, and nobody is emailed twice.
"""

from unittest.mock import patch

import pytest

from gefapi import db
from gefapi.models import OAuthClient, User
from gefapi.models.app_access import (
    STATUS_ACTIVE,
    STATUS_PENDING,
    AppAccessNotificationRecipient,
    UserAppAccess,
)
from gefapi.services.app_access_service import AppAccessError, AppAccessService
from gefapi.tasks.app_access_notifications import (
    notify_superadmins_of_app_access_request,
)
from gefapi.utils.app_access import app_admin_url
from tests.conftest import USER_TEST_PASSWORD

AE = "avoided_emissions"
AE_STAGING = "avoided_emissions_staging"
RIO = "rio_coherence"
APP_SCOPES = "openid email profile app:read account:write app-users:read"


@pytest.fixture(autouse=True)
def _no_rate_limits(rate_limiting_disabled):
    yield


def _user(email, role="USER", active=True, verified=True, name=None):
    user = User(
        email=email,
        password=USER_TEST_PASSWORD,
        name=name or email.split("@")[0],
        role=role,
        country="US",
        institution="Inst",
    )
    user.is_active = active
    user.email_verified = verified
    db.session.add(user)
    db.session.commit()
    return user


def _grant(user, app_key, role="member", status=STATUS_ACTIVE, note=None):
    grant = UserAppAccess(
        user_id=user.id, app_key=app_key, status=status, role=role, request_note=note
    )
    db.session.add(grant)
    db.session.commit()
    return grant


def _client(app_key, approves=True, active=True):
    scopes = APP_SCOPES + (" app-access:write" if approves else "")
    client = OAuthClient(
        name=f"{app_key}-web",
        client_id=f"{app_key}-web",
        redirect_uris="https://app.test/cb",
        post_logout_redirect_uris="",
        audience=f"{app_key}-aud",
        scopes=scopes,
        is_public=False,
        required_app_key=app_key,
    )
    client.is_active = active
    db.session.add(client)
    db.session.commit()
    return client


def _url(app_key):
    return f"/api/v1/admin/app-access/apps/{app_key}/notification-recipients"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def test_listed_recipients_replace_superadmin_default(app, superadmin_user):
    admin = _user("listed-admin@test.org", role="ADMIN")
    AppAccessService.set_notification_recipients(AE, [str(admin.id)], superadmin_user)
    plan = AppAccessService.request_notification_plan(AE)
    assert plan == {
        "te_ui": [admin.email],
        "app_admin": [],
        "using_default": False,
    }
    # Lists are per application.
    rio = AppAccessService.request_notification_plan(RIO)
    assert rio["using_default"] is True
    assert superadmin_user.email in rio["te_ui"]


def test_empty_list_reverts_to_superadmins(app, superadmin_user):
    admin = _user("listed-admin@test.org", role="ADMIN")
    AppAccessService.set_notification_recipients(AE, [str(admin.id)], superadmin_user)
    AppAccessService.set_notification_recipients(AE, [], superadmin_user)
    plan = AppAccessService.request_notification_plan(AE)
    assert plan["using_default"] is True
    assert plan["te_ui"] == AppAccessService.superadmin_recipients()


@pytest.mark.parametrize(
    ("role", "active"), [("USER", True), ("ADMIN", False), ("SUPERADMIN", False)]
)
def test_only_active_admins_can_be_listed(app, superadmin_user, role, active):
    candidate = _user("candidate@test.org", role=role, active=active)
    with pytest.raises(AppAccessError) as exc:
        AppAccessService.set_notification_recipients(
            AE, [str(candidate.id)], superadmin_user
        )
    assert exc.value.status == 400
    assert exc.value.payload["invalid_user_ids"] == [str(candidate.id)]
    assert AppAccessNotificationRecipient.query.count() == 0


def test_unknown_or_malformed_ids_are_rejected(app, superadmin_user):
    with pytest.raises(AppAccessError) as exc:
        AppAccessService.set_notification_recipients(
            AE,
            ["not-a-uuid", "00000000-0000-0000-0000-000000000000"],
            superadmin_user,
        )
    assert set(exc.value.payload["invalid_user_ids"]) == {
        "not-a-uuid",
        "00000000-0000-0000-0000-000000000000",
    }


def test_demoted_listed_user_falls_back_to_default(app, superadmin_user):
    admin = _user("listed-admin@test.org", role="ADMIN")
    AppAccessService.set_notification_recipients(AE, [str(admin.id)], superadmin_user)
    User.query.filter_by(id=admin.id).update({"role": "USER"})
    db.session.commit()
    plan = AppAccessService.request_notification_plan(AE)
    assert plan["using_default"] is True
    assert admin.email not in plan["te_ui"]


def test_auto_app_admins_require_approval_client(app, superadmin_user):
    app_admin = _user("ae-admin@test.org")
    _grant(app_admin, AE, role="admin")
    # No client through which the admin can approve users.
    assert AppAccessService.auto_app_admins(AE) == []
    _client(AE, approves=False)
    assert AppAccessService.auto_app_admins(AE) == []


def test_auto_app_admins_and_destinations(app, superadmin_user):
    _client(AE)
    app_admin = _user("ae-admin@test.org")
    _grant(app_admin, AE, role="admin")
    member = _user("ae-member@test.org")
    _grant(member, AE)
    pending_admin = _user("pending-admin@test.org")
    _grant(pending_admin, AE, role="admin", status=STATUS_PENDING)
    unverified = _user("unverified@test.org", verified=False)
    _grant(unverified, AE, role="admin")
    staging_admin = _user("staging-admin@test.org")
    _grant(staging_admin, AE_STAGING, role="admin")

    assert [u.email for u in AppAccessService.auto_app_admins(AE)] == [app_admin.email]
    plan = AppAccessService.request_notification_plan(AE)
    assert plan["app_admin"] == [app_admin.email]
    assert superadmin_user.email in plan["te_ui"]
    # Staging grants are independent of production.
    assert AppAccessService.auto_app_admins(AE_STAGING) == []


def test_person_in_both_groups_gets_the_api_ui_email_once(app, superadmin_user):
    _client(AE)
    both = _user("Both@Test.org", role="ADMIN")
    _grant(both, AE, role="admin")
    AppAccessService.set_notification_recipients(AE, [str(both.id)], superadmin_user)
    plan = AppAccessService.request_notification_plan(AE)
    assert plan["te_ui"] == [both.email]
    assert plan["app_admin"] == []
    assert AppAccessService.request_notification_recipients(AE) == [both.email]


def test_superadmin_implicit_app_admin_is_not_auto_included(app, superadmin_user):
    _client(AE)
    listed = _user("listed-admin@test.org", role="ADMIN")
    AppAccessService.set_notification_recipients(AE, [str(listed.id)], superadmin_user)
    plan = AppAccessService.request_notification_plan(AE)
    assert superadmin_user.email not in plan["te_ui"] + plan["app_admin"]


def test_changes_are_audited(app, superadmin_user):
    admin = _user("listed-admin@test.org", role="ADMIN")
    with patch("gefapi.services.app_access_service.log_security_event") as log_event:
        AppAccessService.set_notification_recipients(
            AE, [str(admin.id)], superadmin_user
        )
        AppAccessService.set_notification_recipients(
            AE, [str(admin.id)], superadmin_user
        )
    assert log_event.call_count == 1
    assert log_event.call_args.args[0] == "APP_ACCESS_NOTIFY_RECIPIENTS_CHANGED"


def test_deleting_user_removes_listing(app, superadmin_user):
    admin = _user("listed-admin@test.org", role="ADMIN")
    AppAccessService.set_notification_recipients(AE, [str(admin.id)], superadmin_user)
    db.session.delete(admin)
    db.session.commit()
    assert AppAccessNotificationRecipient.query.count() == 0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_routes_are_superadmin_only(app, client, auth_headers_admin, auth_headers_user):
    for headers in (auth_headers_admin, auth_headers_user):
        assert client.get(_url(AE), headers=headers).status_code == 403
        assert (
            client.put(_url(AE), json={"user_ids": []}, headers=headers).status_code
            == 403
        )
    assert client.get(_url(AE)).status_code == 401


def test_route_round_trip(app, client, auth_headers_superadmin, superadmin_user):
    admin = _user("listed-admin@test.org", role="ADMIN", name="Listed")
    response = client.get(_url(AE), headers=auth_headers_superadmin)
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["using_default"] is True
    assert data["listed_recipients"] == []
    assert superadmin_user.email in data["effective_recipients"]

    response = client.put(
        _url(AE), json={"user_ids": [str(admin.id)]}, headers=auth_headers_superadmin
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["using_default"] is False
    assert data["effective_recipients"] == [admin.email]
    [row] = data["listed_recipients"]
    assert row["user_id"] == str(admin.id)
    assert row["role"] == "ADMIN"
    assert row["eligible"] is True


def test_route_validation(app, client, auth_headers_superadmin, regular_user):
    assert client.get(_url("nope"), headers=auth_headers_superadmin).status_code == 404
    assert (
        client.put(
            _url(AE), json={"user_ids": "x"}, headers=auth_headers_superadmin
        ).status_code
        == 400
    )
    response = client.put(
        _url(AE),
        json={"user_ids": [str(regular_user.id)]},
        headers=auth_headers_superadmin,
    )
    assert response.status_code == 400
    assert str(regular_user.id) in response.get_json()["invalid_user_ids"]


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------


def _send(grant):
    with patch("gefapi.services.email_service.EmailService.send_html_email") as send:
        result = notify_superadmins_of_app_access_request.run(grant.id)
    return result, send


def test_email_groups_get_their_own_links(app, superadmin_user, monkeypatch):
    monkeypatch.setenv("API_UI_URL", "https://api-ui.test")
    monkeypatch.setenv("AVOIDED_EMISSIONS_ADMIN_URL", "https://ae.test/admin")
    _client(AE)
    app_admin = _user("ae-admin@test.org")
    _grant(app_admin, AE, role="admin")
    requester = _user("requester@test.org", name="<b>Eve</b>")
    grant = _grant(
        requester, AE, status=STATUS_PENDING, note="<script>alert(1)</script>"
    )

    plan = AppAccessService.request_notification_plan(AE)
    assert superadmin_user.email in plan["te_ui"]
    assert plan["app_admin"] == [app_admin.email]
    result, send = _send(grant)
    assert result == {"status": "success", "recipients": len(plan["te_ui"]) + 1}
    calls = {
        tuple(c.kwargs["recipients"]): c.kwargs["html"] for c in send.call_args_list
    }
    te_html = calls[tuple(plan["te_ui"])]
    ae_html = calls[(app_admin.email,)]
    assert (
        "https://api-ui.test/?tab=app-access&amp;app=avoided_emissions&amp;"
        "status=pending" in te_html
    )
    assert "https://ae.test/admin" in ae_html
    assert "api-ui.test" not in ae_html
    for body in (te_html, ae_html):
        assert "<script>" not in body
        assert "&lt;script&gt;" in body
        assert "&lt;b&gt;Eve&lt;/b&gt;" in body


def test_app_admin_email_without_admin_url_has_no_link(
    app, superadmin_user, monkeypatch
):
    monkeypatch.delenv("AVOIDED_EMISSIONS_ADMIN_URL", raising=False)
    _client(AE)
    app_admin = _user("ae-admin@test.org")
    _grant(app_admin, AE, role="admin")
    grant = _grant(_user("requester@test.org"), AE, status=STATUS_PENDING)
    _, send = _send(grant)
    [ae_call] = [
        c for c in send.call_args_list if c.kwargs["recipients"] == [app_admin.email]
    ]
    assert "<a " not in ae_call.kwargs["html"]


def test_one_failed_group_does_not_block_the_other(app, superadmin_user):
    _client(AE)
    app_admin = _user("ae-admin@test.org")
    _grant(app_admin, AE, role="admin")
    grant = _grant(_user("requester@test.org"), AE, status=STATUS_PENDING)
    with patch(
        "gefapi.services.email_service.EmailService.send_html_email",
        side_effect=[RuntimeError("smtp down"), None],
    ) as send:
        result = notify_superadmins_of_app_access_request.run(grant.id)
    assert send.call_count == 2
    assert result["status"] == "success"
    assert result["failures"] == ["smtp down"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://ae.test/admin", "https://ae.test/admin"),
        ("http://localhost:8050/admin", "http://localhost:8050/admin"),
        ("http://ae.test/admin", None),
        ("https://user:pw@ae.test/admin", None),
        ("javascript:alert(1)", None),
        ("", None),
    ],
)
def test_app_admin_url_validation(monkeypatch, value, expected):
    monkeypatch.setenv("AVOIDED_EMISSIONS_ADMIN_URL", value)
    assert app_admin_url(AE) == expected
