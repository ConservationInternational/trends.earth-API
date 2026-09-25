"""Per-application access grants for gated first-party applications."""

import uuid

from gefapi import db
from gefapi.models import GUID
from gefapi.utils import utcnow

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"

APP_ACCESS_STATUSES = (STATUS_PENDING, STATUS_ACTIVE, STATUS_REVOKED)

DEFAULT_APP_ROLE = "member"


class UserAppAccess(db.Model):
    """A user's access grant to a gated first-party application.

    One row per ``(user_id, app_key)``.  ``status`` controls whether the user
    may sign in to the application at all; ``role`` carries the application's
    own role model so that no application needs a local user store.
    """

    __tablename__ = "user_app_access"
    __table_args__ = (
        db.UniqueConstraint("user_id", "app_key", name="uq_user_app_access_user_app"),
        db.Index("ix_user_app_access_app_key_status", "app_key", "status"),
    )

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(
        GUID(),
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    app_key = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    role = db.Column(db.String(20), nullable=False, default=DEFAULT_APP_ROLE)
    requested_at = db.Column(db.DateTime(), nullable=True)
    request_note = db.Column(db.Text(), nullable=True)
    granted_at = db.Column(db.DateTime(), nullable=True)
    revoked_at = db.Column(db.DateTime(), nullable=True)
    granted_by_user_id = db.Column(
        GUID(), db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    note = db.Column(db.Text(), nullable=True)
    # Throttles the superadmin "new request" notification (see app_access service).
    notified_at = db.Column(db.DateTime(), nullable=True)
    created_at = db.Column(db.DateTime(), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )

    user = db.relationship("User", foreign_keys=[user_id], back_populates="app_access")
    granted_by = db.relationship("User", foreign_keys=[granted_by_user_id])

    def __init__(
        self,
        user_id,
        app_key,
        status=STATUS_PENDING,
        role=DEFAULT_APP_ROLE,
        requested_at=None,
        request_note=None,
        granted_at=None,
        granted_by_user_id=None,
        note=None,
    ):
        self.id = uuid.uuid4()
        self.user_id = user_id
        self.app_key = app_key
        self.status = status
        self.role = role
        self.requested_at = requested_at
        self.request_note = request_note
        self.granted_at = granted_at
        self.granted_by_user_id = granted_by_user_id
        self.note = note
        self.created_at = utcnow()
        self.updated_at = utcnow()

    def __repr__(self):
        return (
            f"<UserAppAccess(user_id={self.user_id}, app_key={self.app_key}, "
            f"status={self.status}, role={self.role})>"
        )

    @property
    def is_active(self):
        return self.status == STATUS_ACTIVE

    def serialize(self):
        """Return object data in easily serializeable format."""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "app_key": self.app_key,
            "status": self.status,
            "role": self.role,
            "requested_at": self.requested_at.isoformat()
            if self.requested_at
            else None,
            "request_note": self.request_note,
            "granted_at": self.granted_at.isoformat() if self.granted_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "granted_by_user_id": str(self.granted_by_user_id)
            if self.granted_by_user_id
            else None,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
