"""BULK EMAIL RECIPIENT LIST MODEL"""

import datetime
import uuid

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID


def _utcnow():
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


class BulkEmailRecipientList(db.Model):
    """
    Stores named recipient groups with JSON filter criteria.

    Filter criteria are resolved fresh against the live user table at send time,
    so recipient counts may differ from the cached estimated_count.
    """

    __tablename__ = "bulk_email_recipient_list"

    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,
    )
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    # JSON filter object: {roles, min_created_at, max_created_at,
    #                      min_last_activity_at, max_last_activity_at,
    #                      email_verified}
    filter_criteria = db.Column(db.JSON, nullable=False, default=dict)
    # Cached count resolved at save time â€” may be stale
    estimated_count = db.Column(db.Integer, nullable=True)
    created_by_id = db.Column(db.GUID(), db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime(), default=_utcnow)
    updated_at = db.Column(db.DateTime(), default=_utcnow, onupdate=_utcnow)

    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<BulkEmailRecipientList name={self.name!r} id={self.id!r}>"
