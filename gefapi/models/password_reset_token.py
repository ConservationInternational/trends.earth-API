"""PASSWORD RESET TOKEN MODEL

Provides secure time-limited tokens for password reset functionality.
Tokens expire after 1 hour and can only be used once.
"""

import datetime
import logging
import secrets
import uuid

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID

logger = logging.getLogger(__name__)

# Token expiry time in hours
PASSWORD_RESET_TOKEN_EXPIRY_HOURS = 1


class PasswordResetToken(db.Model):
    """Password Reset Token Model

    Stores secure tokens for password reset requests. Each token:
    - Has a 1-hour expiry window
    - Can only be used once
    - Is associated with a single user
    - Uses cryptographically secure random generation
    """

    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,
    )
    # Cryptographically secure token (64 characters, URL-safe)
    token = db.Column(db.String(128), unique=True, nullable=False, index=True)
    user_id = db.Column(db.GUID(), db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(
        db.DateTime(), default=lambda: datetime.datetime.now(datetime.UTC)
    )
    expires_at = db.Column(db.DateTime(), nullable=False)
    used_at = db.Column(db.DateTime(), nullable=True)  # Track when token was used

    # Relationship to user
    user = db.relationship("User", backref=db.backref("password_reset_tokens"))

    def __init__(self, user_id):
        self.user_id = user_id
        self.token = self._generate_secure_token()
        self.created_at = datetime.datetime.now(datetime.UTC)
        self.expires_at = self.created_at + datetime.timedelta(
            hours=PASSWORD_RESET_TOKEN_EXPIRY_HOURS
        )

    def __repr__(self):
        return f"<PasswordResetToken user_id={self.user_id!r}>"

    @staticmethod
    def _generate_secure_token():
        """Generate a cryptographically secure URL-safe token."""
        return secrets.token_urlsafe(48)

    def is_valid(self):
        """Check if the token is valid (not expired and not used)."""
        now = datetime.datetime.now(datetime.UTC)
        return self.used_at is None and self.expires_at > now

    def mark_used(self):
        """Mark the token as used."""
        self.used_at = datetime.datetime.now(datetime.UTC)

    @classmethod
    def get_valid_token(cls, token_string):
        """Find a valid (unexpired, unused) token by its string value.

        Args:
            token_string: The token string to look up

        Returns:
            PasswordResetToken if found and valid, None otherwise
        """
        token = cls.query.filter_by(token=token_string).first()
        if token and token.is_valid():
            return token
        return None

    @classmethod
    def invalidate_user_tokens(cls, user_id):
        """Invalidate all existing tokens for a user.

        Called when creating a new reset token to ensure only one
        valid token exists per user at a time.
        """
        now = datetime.datetime.now(datetime.UTC)
        cls.query.filter(
            cls.user_id == user_id,
            cls.used_at.is_(None),
            cls.expires_at > now,
        ).update({"used_at": now}, synchronize_session=False)

    @classmethod
    def cleanup_expired_tokens(cls, days_old=7):
        """Remove tokens older than the specified number of days.

        Args:
            days_old: Remove tokens older than this many days (default 7)

        Returns:
            Number of tokens deleted
        """
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_old)
        result = cls.query.filter(cls.created_at < cutoff).delete(
            synchronize_session=False
        )
        db.session.commit()
        return result
