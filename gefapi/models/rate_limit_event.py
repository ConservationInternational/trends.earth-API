"""Model for tracking rate limit events."""

from __future__ import annotations

import datetime
import uuid

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID


class RateLimitEvent(db.Model):
    """Persisted record for a rate limit breach."""

    __tablename__ = "rate_limit_event"

    id = db.Column(
        db.GUID(),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        autoincrement=False,
    )
    occurred_at = db.Column(
        db.DateTime(),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
    user_id = db.Column(db.GUID(), db.ForeignKey("user.id"), nullable=True)
    user_role = db.Column(db.String(20), nullable=True)
    user_email = db.Column(db.String(255), nullable=True)
    rate_limit_type = db.Column(db.String(50), nullable=False)
    endpoint = db.Column(db.String(255), nullable=False)
    method = db.Column(db.String(10), nullable=True)
    limit_definition = db.Column(db.String(120), nullable=True)
    limit_count = db.Column(db.Integer(), nullable=True)
    time_window_seconds = db.Column(db.Integer(), nullable=True)
    retry_after_seconds = db.Column(db.Integer(), nullable=True)
    limit_key = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)

    user = db.relationship(
        "User", backref=db.backref("rate_limit_events", lazy="dynamic")
    )

    def serialize(self) -> dict[str, object]:
        """Serialize event data for API responses."""

        return {
            "id": str(self.id) if self.id else None,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "user_role": self.user_role,
            "user_email": self.user_email,
            "rate_limit_type": self.rate_limit_type,
            "endpoint": self.endpoint,
            "method": self.method,
            "limit_definition": self.limit_definition,
            "limit_count": self.limit_count,
            "time_window_seconds": self.time_window_seconds,
            "retry_after_seconds": self.retry_after_seconds,
            "limit_key": self.limit_key,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
        }
