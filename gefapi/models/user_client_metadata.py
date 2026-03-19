"""User client metadata model for tracking client version usage."""

from datetime import UTC, datetime
import uuid

from gefapi import db
from gefapi.models import GUID


class UserClientMetadata(db.Model):
    """Tracks client platform and version information per user.

    Stores one row per user per client type (e.g., qgis_plugin, api_ui, cli).
    Updated whenever a user makes an authenticated request with the X-TE-Client header.
    """

    __tablename__ = "user_client_metadata"
    __table_args__ = (
        db.UniqueConstraint("user_id", "client_type", name="uq_user_client_type"),
    )

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(GUID(), db.ForeignKey("user.id"), nullable=False, index=True)
    client_type = db.Column(db.String(50), nullable=False)  # qgis_plugin, api_ui, cli
    client_version = db.Column(db.String(50), nullable=True)  # e.g., 2.2.4
    os = db.Column(db.String(50), nullable=True)  # Windows, macOS, Linux (plugin only)
    qgis_version = db.Column(db.String(20), nullable=True)  # e.g., 3.34.0 (plugin only)
    language = db.Column(db.String(10), nullable=True)  # e.g., en, es, fr
    extra_metadata = db.Column(
        db.JSON, nullable=True
    )  # Future-proofing for unknown fields
    last_seen_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    # Relationship
    user = db.relationship("User", back_populates="client_metadata")

    def __init__(
        self,
        user_id,
        client_type,
        client_version=None,
        os=None,
        qgis_version=None,
        language=None,
        extra_metadata=None,
    ):
        self.id = uuid.uuid4()
        self.user_id = user_id
        self.client_type = client_type
        self.client_version = client_version
        self.os = os
        self.qgis_version = qgis_version
        self.language = language
        self.extra_metadata = extra_metadata
        self.last_seen_at = datetime.now(UTC)
        self.created_at = datetime.now(UTC)

    def __repr__(self):
        return (
            f"<UserClientMetadata(user_id={self.user_id}, "
            f"client_type={self.client_type}, version={self.client_version})>"
        )

    def update_from_header(
        self,
        client_version=None,
        os=None,
        qgis_version=None,
        language=None,
        extra_metadata=None,
    ):
        """Update metadata from a new X-TE-Client header."""
        if client_version:
            self.client_version = client_version
        if os:
            self.os = os
        if qgis_version:
            self.qgis_version = qgis_version
        if language:
            self.language = language
        if extra_metadata:
            self.extra_metadata = extra_metadata
        self.last_seen_at = datetime.now(UTC)

    def serialize(self):
        """Serialize for API responses."""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "client_type": self.client_type,
            "client_version": self.client_version,
            "os": self.os,
            "qgis_version": self.qgis_version,
            "language": self.language,
            "extra_metadata": self.extra_metadata,
            "last_seen_at": self.last_seen_at.isoformat()
            if self.last_seen_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
