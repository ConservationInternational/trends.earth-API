"""BULK EMAIL MODEL"""

import datetime
import uuid

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID


def _utcnow():
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


class BulkEmail(db.Model):
    """
    Stores bulk email drafts and sent bulk emails.

    Bulk emails have a lifecycle: DRAFT -> SENT.
    The html_content is stored in full at both draft-save time and send time.
    SparkPost substitution markers (e.g. {{name}}, {{email}}) are resolved
    per-recipient at send time via substitution_data.
    """

    __tablename__ = "bulk_email"

    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,
    )
    name = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(500), nullable=False)
    html_content = db.Column(db.Text, nullable=False)
    # "DRAFT" or "SENT"
    status = db.Column(db.String(10), nullable=False, default="DRAFT")
    recipient_list_id = db.Column(
        db.GUID(),
        db.ForeignKey("bulk_email_recipient_list.id"),
        nullable=True,
    )
    # Set at send time after resolving the full recipient query
    recipient_count = db.Column(db.Integer, nullable=True)
    created_by_id = db.Column(db.GUID(), db.ForeignKey("user.id"), nullable=False)
    sent_by_id = db.Column(db.GUID(), db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime(), default=_utcnow)
    updated_at = db.Column(db.DateTime(), default=_utcnow, onupdate=_utcnow)
    sent_at = db.Column(db.DateTime(), nullable=True)

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    sent_by = db.relationship("User", foreign_keys=[sent_by_id])
    recipient_list = db.relationship(
        "BulkEmailRecipientList",
        foreign_keys=[recipient_list_id],
    )

    def __repr__(self):
        return f"<BulkEmail name={self.name!r} status={self.status!r} id={self.id!r}>"
