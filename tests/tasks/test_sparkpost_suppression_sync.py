"""Tests for SparkPost suppression synchronization."""

from io import BytesIO
import json
from unittest.mock import patch

from gefapi import db
from gefapi.models import User
from gefapi.tasks.sparkpost_suppression_sync import (
    _recent_tracking_events,
    sync_sparkpost_suppressions,
)


def _user(email, *, news=True, engagement=True):
    return User(
        email=email,
        password="ValidPass123!",
        name="Suppression Test User",
        country="Test Country",
        institution="Test Institution",
        role="USER",
        email_subscription_news=news,
        email_subscription_engagement=engagement,
        email_subscription_system_updates=True,
        email_notifications_enabled=True,
    )


@patch.dict("os.environ", {"SPARKPOST_API_KEY": "test-key"})
@patch("gefapi.tasks.sparkpost_suppression_sync._recent_tracking_events")
def test_sync_disables_all_bulk_email_preferences(mock_suppressions, app):
    mock_suppressions.return_value = [
        {"rcpt_to": "USER@EXAMPLE.COM", "type": "list_unsubscribe"},
        {"rcpt_to": "user@example.com", "type": "link_unsubscribe"},
        {"rcpt_to": "missing@example.com", "type": "bounce", "bounce_class": "10"},
        {"rcpt_to": "ignored@example.com", "type": "bounce", "bounce_class": "22"},
    ]

    with app.app_context():
        user = _user("user@example.com")
        db.session.add(user)
        db.session.commit()

        result = sync_sparkpost_suppressions.run()
        db.session.refresh(user)

        assert result == {"records": 4, "matched_users": 1, "updated_users": 1}
        assert user.email_subscription_news is False
        assert user.email_subscription_engagement is False
        assert user.email_subscription_system_updates is False
        assert user.email_notifications_enabled is True
        assert user.consent_given_at is not None
        assert user.consent_source == "sparkpost_suppression"


@patch.dict("os.environ", {}, clear=True)
def test_sync_skips_when_sparkpost_is_not_configured(app):
    with app.app_context():
        assert sync_sparkpost_suppressions.run() == {
            "records": 0,
            "matched_users": 0,
            "updated_users": 0,
        }


def test_suppression_sync_is_scheduled(app):
    from gefapi import celery

    assert (
        "gefapi.tasks.sparkpost_suppression_sync.sync_sparkpost_suppressions"
        in celery.tasks
    )
    schedule = celery.conf.beat_schedule["sync-sparkpost-suppressions"]
    assert schedule["task"] == (
        "gefapi.tasks.sparkpost_suppression_sync.sync_sparkpost_suppressions"
    )
    assert schedule["options"]["queue"] == "default"


@patch("gefapi.tasks.sparkpost_suppression_sync.urlopen")
def test_recent_tracking_events_follows_sparkpost_pagination(mock_urlopen):
    first_page = {
        "results": [{"event_id": "first"}],
        "links": {"next": "/api/v1/events/message?cursor=next-page"},
    }
    second_page = {"results": [{"event_id": "second"}], "links": {}}
    mock_urlopen.side_effect = [
        BytesIO(json.dumps(first_page).encode()),
        BytesIO(json.dumps(second_page).encode()),
    ]

    events = _recent_tracking_events("secret-key", 24)

    assert events == [{"event_id": "first"}, {"event_id": "second"}]
    assert mock_urlopen.call_count == 2
    for call in mock_urlopen.call_args_list:
        request = call.args[0]
        assert request.full_url.startswith("https://api.sparkpost.com/api/v1/events/")
        assert request.get_header("Authorization") == "secret-key"
