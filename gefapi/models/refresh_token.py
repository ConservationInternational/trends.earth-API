"""REFRESH TOKEN MODEL"""

import datetime
import secrets
import uuid

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID


class RefreshToken(db.Model):
    """Refresh Token Model"""

    __tablename__ = "refresh_tokens"

    id = db.Column(
        GUID(), primary_key=True, default=uuid.uuid4, nullable=False
    )
    user_id = db.Column(GUID(), db.ForeignKey("user.id"), nullable=False, index=True)
    token = db.Column(db.String(255), nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(
        db.DateTime, default=datetime.datetime.utcnow, nullable=False
    )
    is_revoked = db.Column(db.Boolean, default=False, nullable=False)
    device_info = db.Column(db.String(500))  # Store user agent, IP, etc.
    last_used_at = db.Column(db.DateTime)

    __table_args__ = (
        db.UniqueConstraint('token', name='refresh_tokens_token_key'),
    )

    # Relationship
    user = db.relationship("User", backref=db.backref("refresh_tokens", lazy=True))

    def __init__(self, user_id, expires_at=None, device_info=None):
        self.user_id = user_id
        self.token = self.generate_token()
        self.expires_at = expires_at or self.default_expiry()
        self.device_info = device_info

    def __repr__(self):
        return f"<RefreshToken {self.id}>"

    @staticmethod
    def generate_token():
        """Generate a secure random token"""
        return secrets.token_urlsafe(32)

    @staticmethod
    def default_expiry():
        """Default expiry time (30 days from now)"""
        return datetime.datetime.utcnow() + datetime.timedelta(days=30)

    def is_valid(self):
        """Check if token is valid (not expired and not revoked)"""
        return not self.is_revoked and self.expires_at > datetime.datetime.utcnow()

    def revoke(self):
        """Revoke the refresh token"""
        self.is_revoked = True

    def update_last_used(self):
        """Update last used timestamp"""
        self.last_used_at = datetime.datetime.utcnow()

    def serialize(self):
        """Return object data in easily serializable format"""
        return {
            "id": str(self.id),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat()
            if self.last_used_at
            else None,
            "device_info": self.device_info,
            "is_revoked": self.is_revoked,
        }
