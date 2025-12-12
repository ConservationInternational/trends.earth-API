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

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4, nullable=False)
    user_id = db.Column(GUID(), db.ForeignKey("user.id"), nullable=False, index=True)
    token = db.Column(db.String(255), nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )
    is_revoked = db.Column(db.Boolean, default=False, nullable=False)
    device_info = db.Column(db.String(500))  # Store user agent, IP, etc.
    last_used_at = db.Column(db.DateTime)

    __table_args__ = (db.UniqueConstraint("token", name="refresh_tokens_token_key"),)

    # Relationship - backref handled by User model
    user = db.relationship("User", back_populates="user_refresh_tokens")

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
        return datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)

    def is_valid(self, verify_client_ip=False, current_ip=None):
        """Check if token is valid (not expired and not revoked).

        Args:
            verify_client_ip: If True, optionally verify the client IP matches
            current_ip: Current client IP address for verification

        Returns:
            bool: True if token is valid, False otherwise
        """
        if self.is_revoked or self.expires_at <= datetime.datetime.now(datetime.UTC):
            return False

        # Optional client IP verification for additional security
        if verify_client_ip and current_ip and self.device_info:
            stored_ip = self._extract_ip_from_device_info()
            if stored_ip and stored_ip != current_ip:
                # Log suspicious activity but don't fail by default
                # This provides visibility without breaking existing clients
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(
                    f"Token IP mismatch: stored={stored_ip}, current={current_ip}, "
                    f"token_id={self.id}"
                )

        return True

    def _extract_ip_from_device_info(self):
        """Extract IP address from stored device_info string."""
        if not self.device_info:
            return None
        # device_info format: "IP: x.x.x.x | UA: ..."
        if self.device_info.startswith("IP: "):
            parts = self.device_info.split(" | ")
            if parts:
                return parts[0].replace("IP: ", "").strip()
        return None

    def get_client_fingerprint(self):
        """Get a fingerprint of the client that created this token."""
        return {
            "ip_address": self._extract_ip_from_device_info(),
            "device_info": self.device_info,
        }

    def revoke(self):
        """Revoke the refresh token"""
        self.is_revoked = True

    def update_last_used(self):
        """Update last used timestamp"""
        self.last_used_at = datetime.datetime.now(datetime.UTC)

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
