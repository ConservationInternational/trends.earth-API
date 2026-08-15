"""Synchronize recent SparkPost suppressions with marketing preferences."""

import datetime
import json
import logging
import os
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from celery import Task
from sqlalchemy import func

from gefapi import celery, db
from gefapi.config import SETTINGS
from gefapi.models import User
from gefapi.utils import utcnow

logger = logging.getLogger(__name__)

_MARKETING_OPT_OUT_EVENTS = {
    "list_unsubscribe",
    "link_unsubscribe",
    "spam_complaint",
}
_INVALID_RECIPIENT_BOUNCE_CLASS = "10"
_SPARKPOST_EVENTS_URL = "https://api.sparkpost.com/api/v1/events/message"


class SparkPostSuppressionSyncTask(Task):
    """Report SparkPost synchronization failures through normal task logging."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.exception("SparkPost suppression synchronization failed: %s", exc)


def _recent_tracking_events(api_key, lookback_hours):
    """Return recent opt-out and bounce events, following SparkPost pagination."""
    end = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
    start = end - datetime.timedelta(hours=lookback_hours)
    timestamp_format = "%Y-%m-%dT%H:%M:%SZ"
    query = urlencode(
        {
            "from": start.strftime(timestamp_format),
            "to": end.strftime(timestamp_format),
            "events": "list_unsubscribe,link_unsubscribe,spam_complaint,bounce",
            "per_page": 10000,
            "cursor": "initial",
        }
    )
    next_url = f"{_SPARKPOST_EVENTS_URL}?{query}"
    events = []

    while next_url:
        parsed_url = urlparse(next_url)
        if parsed_url.scheme != "https" or parsed_url.netloc != "api.sparkpost.com":
            raise ValueError("SparkPost returned an invalid Events API pagination URL")
        request = Request(  # noqa: S310
            next_url,
            headers={"Authorization": api_key, "Accept": "application/json"},
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310
            body = json.load(response)
        events.extend(body.get("results", []))
        next_link = body.get("links", {}).get("next")
        next_url = urljoin(_SPARKPOST_EVENTS_URL, next_link) if next_link else None

    return events


def _should_disable_bulk_email(event):
    """Return whether an event represents an opt-out or nonexistent address."""
    event_type = event.get("type")
    return event_type in _MARKETING_OPT_OUT_EVENTS or (
        event_type == "bounce"
        and str(event.get("bounce_class")) == _INVALID_RECIPIENT_BOUNCE_CLASS
    )


@celery.task(base=SparkPostSuppressionSyncTask, bind=True)
def sync_sparkpost_suppressions(self):
    """Disable bulk-email subscriptions for recently suppressed recipients.

    Automated job notifications are deliberately unchanged because they are
    transactional. System updates, news, and engagement email are disabled.
    The overlapping lookback window makes the task safe to retry without a
    database cursor because setting the three subscription flags to false is
    idempotent.
    """
    api_key = os.getenv("SPARKPOST_API_KEY")
    if not api_key:
        logger.info("Skipping SparkPost suppression sync: API key is not configured")
        return {"records": 0, "matched_users": 0, "updated_users": 0}

    lookback_hours = SETTINGS.get("SPARKPOST_SUPPRESSION_LOOKBACK_HOURS", 24)
    records = _recent_tracking_events(api_key, lookback_hours) or []
    suppressed_addresses = {
        record.get("rcpt_to", "").strip().lower()
        for record in records
        if _should_disable_bulk_email(record) and record.get("rcpt_to", "").strip()
    }

    if not suppressed_addresses:
        return {"records": len(records), "matched_users": 0, "updated_users": 0}

    users = User.query.filter(func.lower(User.email).in_(suppressed_addresses)).all()
    updated_users = 0
    for user in users:
        if (
            user.email_subscription_news
            or user.email_subscription_engagement
            or user.email_subscription_system_updates
        ):
            user.email_subscription_news = False
            user.email_subscription_engagement = False
            user.email_subscription_system_updates = False
            user.consent_given_at = utcnow()
            user.consent_source = "sparkpost_suppression"
            updated_users += 1

    if updated_users:
        db.session.commit()

    logger.info(
        "SparkPost suppression sync processed %d records, matched %d users, "
        "and disabled bulk email for %d users",
        len(records),
        len(users),
        updated_users,
    )
    return {
        "records": len(records),
        "matched_users": len(users),
        "updated_users": updated_users,
    }
