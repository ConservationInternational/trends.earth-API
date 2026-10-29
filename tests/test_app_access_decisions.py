"""User emails for application access approval and denial."""

from unittest.mock import patch

import pytest

from gefapi import db
from gefapi.models.app_access import (
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REVOKED,
    UserAppAccess,
)
from gefapi.services.app_access_service import AppAccessService
from gefapi.tasks.app_access_notifications import notify_user_of_app_access_decision
from gefapi.utils.app_access import app_label

TASK = "gefapi.tasks.app_access_notifications.notify_user_of_app_access_decision"
SEND_EMAIL = "gefapi.services.email_service.EmailService.send_html_email"


@pytest.fixture
def pending_grant(app, regular_user):
    grant = UserAppAccess(
        user_id=regular_user.id,
        app_key="avoided_emissions",
        status=STATUS_PENDING,
    )
    db.session.add(grant)
    db.session.commit()
    return grant


@pytest.mark.parametrize("status", [STATUS_ACTIVE, STATUS_REVOKED])
@pytest.mark.parametrize("notifications_enabled", [True, False])
@pytest.mark.parametrize(
    "app_key", ["avoided_emissions", "avoided_emissions_staging", "rio_coherence"]
)
def test_admin_decision_sends_user_email(
    client,
    auth_headers_admin,
    regular_user,
    pending_grant,
    status,
    app_key,
    notifications_enabled,
):
    pending_grant.app_key = app_key
    user = db.session.merge(regular_user)
    user.name = "<Reviewer & User>"
    user.email_notifications_enabled = notifications_enabled
    db.session.commit()

    with (
        patch(
            f"{TASK}.delay", side_effect=notify_user_of_app_access_decision.run
        ) as enqueue,
        patch(SEND_EMAIL) as send,
    ):
        url = f"/api/v1/admin/users/{regular_user.id}/app-access"
        if status == STATUS_ACTIVE:
            response = client.post(
                url,
                headers=auth_headers_admin,
                json={"app_key": app_key, "status": status},
            )
        else:
            response = client.delete(f"{url}/{app_key}", headers=auth_headers_admin)

        assert response.status_code == 200
        assert response.get_json()["data"]["status"] == status
        enqueue.assert_called_once_with(str(pending_grant.id), status)
        send.assert_called_once()
        email = send.call_args.kwargs
        assert email["recipients"] == [regular_user.email]
        assert email["transactional"] is True
        assert app_label(app_key) in email["subject"]
        assert "&lt;Reviewer &amp; User&gt;" in email["html"]
        if status == STATUS_ACTIVE:
            assert "activated" in email["html"]
            assert "You can now sign in" in email["html"]
        else:
            assert "not currently active" in email["html"]
            assert "contact an administrator" in email["html"]

        AppAccessService.set_access(regular_user.id, app_key, status)
        assert enqueue.call_count == 1
        assert send.call_count == 1


@pytest.mark.parametrize("status", [STATUS_ACTIVE, STATUS_REVOKED])
def test_decision_email_ignores_notification_preference(
    regular_user, pending_grant, status
):
    user = db.session.merge(regular_user)
    user.email_notifications_enabled = False
    db.session.commit()

    with patch(SEND_EMAIL) as send:
        result = notify_user_of_app_access_decision.run(str(pending_grant.id), status)

    assert result == {"status": "success"}
    send.assert_called_once()
    assert send.call_args.kwargs["recipients"] == [regular_user.email]
    assert send.call_args.kwargs["transactional"] is True


def test_role_only_change_does_not_send_decision_email(regular_user, pending_grant):
    pending_grant.status = STATUS_ACTIVE
    db.session.commit()

    with patch(f"{TASK}.delay") as enqueue:
        AppAccessService.set_access(
            regular_user.id, pending_grant.app_key, STATUS_ACTIVE, role="admin"
        )

    enqueue.assert_not_called()


def test_deleted_grant_does_not_send_decision_email(pending_grant):
    grant_id = str(pending_grant.id)
    db.session.delete(pending_grant)
    db.session.commit()

    with patch(SEND_EMAIL) as send:
        result = notify_user_of_app_access_decision.run(grant_id, STATUS_ACTIVE)

    assert result == {"status": "skipped", "reason": "grant_not_found"}
    send.assert_not_called()


def test_decision_email_failure_is_reported(pending_grant, caplog):
    with patch(SEND_EMAIL, side_effect=RuntimeError("Email provider unavailable")):
        result = notify_user_of_app_access_decision.run(
            str(pending_grant.id), STATUS_ACTIVE
        )

    assert result == {"status": "failed", "reason": "Email provider unavailable"}
    assert "Failed to send app access decision notification" in caplog.text
