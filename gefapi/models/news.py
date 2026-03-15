"""NEWS MODEL"""

import datetime
import uuid

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID


class NewsItem(db.Model):
    """
    NewsItem Model for displaying announcements and updates to users.

    News items can be targeted to specific platforms (QGIS plugin, web app,
    api-ui), specific user roles, and filtered by plugin version ranges.
    Users can dismiss news items, which is tracked per-user.

    Attributes:
        id: Unique identifier (UUID)
        title: Short headline for the news item
        message: Full message content (can contain HTML/markdown)
        link_url: Optional URL for "Read more" or action link
        link_text: Text to display for the link (default: "Learn more")
        created_at: When the news item was created
        publish_at: When the news item should start being shown
        expires_at: When the news item should stop being shown (null = never)
        target_platforms: Comma-separated list of platforms (app,webapp,api-ui)
        target_roles: Comma-separated roles (USER,ADMIN,SUPERADMIN) or empty
        min_version: Minimum plugin version to show this news item
        max_version: Maximum plugin version to show this news item
        is_active: Whether the news item is active (can be toggled by admins)
        priority: Display priority (higher = more prominent, default 0)
        created_by_id: User ID of the admin who created this item
        news_type: Type of news (info, warning, alert, update)
    """

    __tablename__ = "news_item"

    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,
    )
    title = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text(), nullable=False)
    link_url = db.Column(db.String(500), nullable=True)
    link_text = db.Column(db.String(100), nullable=True, default="Learn more")

    created_at = db.Column(
        db.DateTime(),
        default=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(),
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )
    publish_at = db.Column(
        db.DateTime(),
        default=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )
    expires_at = db.Column(db.DateTime(), nullable=True)

    # Targeting fields
    target_platforms = db.Column(
        db.String(100), nullable=False, default="app,webapp,api-ui"
    )
    target_roles = db.Column(
        db.String(100), nullable=True, default=None
    )  # Comma-separated roles: USER,ADMIN,SUPERADMIN. None means all roles.
    min_version = db.Column(db.String(20), nullable=True)
    max_version = db.Column(db.String(20), nullable=True)

    # Status and display
    is_active = db.Column(db.Boolean(), default=True, nullable=False)
    priority = db.Column(db.Integer(), default=0, nullable=False)
    news_type = db.Column(db.String(20), default="info", nullable=False)

    # Tracking
    created_by_id = db.Column(db.GUID(), db.ForeignKey("user.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __init__(
        self,
        title,
        message,
        link_url=None,
        link_text=None,
        publish_at=None,
        expires_at=None,
        target_platforms="app,webapp,api-ui",
        target_roles=None,
        min_version=None,
        max_version=None,
        is_active=True,
        priority=0,
        news_type="info",
        created_by_id=None,
    ):
        self.id = str(uuid.uuid4())
        self.title = title
        self.message = message
        self.link_url = link_url
        self.link_text = link_text or "Learn more"
        self.publish_at = publish_at or datetime.datetime.now(datetime.UTC)
        self.expires_at = expires_at
        self.target_platforms = target_platforms
        self.target_roles = target_roles
        self.min_version = min_version
        self.max_version = max_version
        self.is_active = is_active
        self.priority = priority
        self.news_type = news_type
        self.created_by_id = created_by_id

    def serialize(self):
        """Serialize news item to dictionary."""
        return {
            "id": str(self.id),
            "title": self.title,
            "message": self.message,
            "link_url": self.link_url,
            "link_text": self.link_text,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "publish_at": self.publish_at.isoformat() if self.publish_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "target_platforms": self.target_platforms.split(",")
            if self.target_platforms
            else [],
            "target_roles": self.target_roles.split(",") if self.target_roles else [],
            "min_version": self.min_version,
            "max_version": self.max_version,
            "is_active": self.is_active,
            "priority": self.priority,
            "news_type": self.news_type,
            "created_by_id": str(self.created_by_id) if self.created_by_id else None,
        }

    def is_applicable_to_platform(self, platform):
        """Check if this news item applies to a specific platform."""
        if not self.target_platforms:
            return True
        platforms = [p.strip().lower() for p in self.target_platforms.split(",")]
        return platform.lower() in platforms

    def is_applicable_to_role(self, role):
        """Check if this news item applies to a specific user role.

        Args:
            role: User role string (USER, ADMIN, SUPERADMIN) or None for unauthenticated

        Returns:
            True if news applies to this role, False otherwise.
            Empty/None target_roles means news applies to all users.
        """
        # No role restrictions means visible to everyone
        if not self.target_roles:
            return True
        # Unauthenticated users can only see news with no role restrictions
        if not role:
            return False
        roles = [r.strip().upper() for r in self.target_roles.split(",")]
        return role.upper() in roles

    def is_applicable_to_version(self, version):
        """Check if this news item applies to a specific plugin version."""
        if not version:
            return True

        from packaging import version as pkg_version

        try:
            v = pkg_version.parse(version)

            if self.min_version:
                min_v = pkg_version.parse(self.min_version)
                if v < min_v:
                    return False

            if self.max_version:
                max_v = pkg_version.parse(self.max_version)
                if v > max_v:
                    return False

            return True
        except Exception:
            # If version parsing fails, include the news item
            return True

    def is_currently_published(self):
        """Check if the news item is currently within its publish window."""
        now = datetime.datetime.now(datetime.UTC)

        if self.publish_at and now < self.publish_at:
            return False

        return not (self.expires_at and now > self.expires_at)
