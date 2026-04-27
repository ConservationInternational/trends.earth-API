"""BULK EMAIL VERIFICATION TOKEN MODEL

Provides 6-digit OTP tokens for confirming large bulk email sends.
Tokens expire after 15 minutes and can only be used once.
Tokens are scoped to a specific (user, bulk_email) pair to prevent replay.
"""

import datetime
import secrets
import uuid

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID

BULK_EMAIL_VERIFICATION_TOKEN_EXPIRY_MINUTES = 15


class BulkEmailVerificationToken(db.Model):
    """6-digit OTP token required to confirm a bulk email send above the threshold."""

    __tablename__ = "bulk_email_verification_token"

    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,
    )
    # 6-digit numeric OTP (stored as plain string â€” not a secret long enough to hash)
    token = db.Column(db.String(6), nullable=False)
    user_id = db.Column(db.GUID(), db.ForeignKey("user.id"), nullable=False)
    bulk_email_id = db.Column(
        db.GUID(), db.ForeignKey("bulk_email.id"), nullable=False
    )
    created_at = db.Column(
        db.DateTime(),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
    )
    expires_at = db.Column(db.DateTime(), nullable=False)
    used_at = db.Column(db.DateTime(), nullable=True)

    user = db.relationship("User", foreign_keys=[user_id])
    bulk_email = db.relationship("BulkEmail", foreign_keys=[bulk_email_id])

    def __init__(self, user_id, bulk_email_id):
        self.user_id = user_id
        self.bulk_email_id = bulk_email_id
        self.token = self._generate_otp()
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        self.created_at = now
        self.expires_at = now + datetime.timedelta(
            minutes=BULK_EMAIL_VERIFICATION_TOKEN_EXPIRY_MINUTES
        )

    @staticmethod
    def _generate_otp():
        """Generate a 6-digit cryptographically secure numeric OTP."""
        return "".join(secrets.choice("0123456789") for _ in range(6))

    @property
    def is_expired(self):
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        return now > self.expires_at

    @property
    def is_used(self):
        return self.used_at is not None

    def __repr__(self):
        return (
            f"<BulkEmailVerificationToken "
            f"user_id={self.user_id!r} bulk_email_id={self.bulk_email_id!r}>"
        )
