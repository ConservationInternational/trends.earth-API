"""USER MODEL"""

import datetime
import logging
import uuid

from flask_jwt_extended import create_access_token
from werkzeug.security import check_password_hash, generate_password_hash

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID

logger = logging.getLogger(__name__)


class User(db.Model):
    """User Model"""

    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,
    )
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    country = db.Column(db.String(120))
    institution = db.Column(db.String(120))
    password = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    role = db.Column(db.String(10))
    scripts = db.relationship(
        "Script",
        backref=db.backref("user"),
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    executions = db.relationship(
        "Execution",
        backref=db.backref("user"),
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    # Google Groups opt-in fields
    google_groups_trends_earth_users = db.Column(
        db.Boolean(), default=False, nullable=False
    )
    google_groups_trendsearth = db.Column(db.Boolean(), default=False, nullable=False)
    google_groups_registration_status = db.Column(
        db.Text(), default=None
    )  # JSON status
    google_groups_last_sync = db.Column(db.DateTime(), default=None)

    # Email notification preferences
    email_notifications_enabled = db.Column(db.Boolean(), default=True, nullable=False)

    def __init__(self, email, password, name, country, institution, role="USER"):
        self.email = email
        self.password = self.set_password(password)
        self.role = role if role in ["USER", "ADMIN", "SUPERADMIN"] else "USER"
        self.name = name
        self.country = country
        self.institution = institution

    def __repr__(self):
        return f"<User {self.email!r}>"

    def serialize(self, include=None, exclude=None):
        """Return object data in easily serializeable format"""
        include = include if include else []
        exclude = exclude if exclude else []
        user = {
            "id": self.id,
            "email": self.email,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "role": self.role,
            "name": self.name,
            "country": self.country,
            "institution": self.institution,
            "email_notifications_enabled": self.email_notifications_enabled,
        }

        # Include Google Groups preferences if requested
        if "google_groups" in include:
            user["google_groups"] = {
                "trends_earth_users": self.google_groups_trends_earth_users,
                "trendsearth": self.google_groups_trendsearth,
                "registration_status": self.google_groups_registration_status,
                "last_sync": self.google_groups_last_sync.isoformat()
                if self.google_groups_last_sync
                else None,
            }

        if "scripts" in include:
            user["scripts"] = self.serialize_scripts

        # Remove excluded fields
        for field in exclude:
            user.pop(field, None)

        return user

    @property
    def serialize_scripts(self):
        """Serialize Scripts"""
        return [item.serialize() for item in self.scripts]

    def set_password(self, password):
        return generate_password_hash(password)

    def check_password(self, password):
        """Check if provided password matches stored hash"""
        if not self.password:
            logger.warning(f"User {self.email} has no password hash stored")
            return False

        if not password:
            logger.debug("Empty password provided for authentication")
            return False

        try:
            return check_password_hash(self.password, password)
        except ValueError as e:
            logger.error(f"Invalid password hash for user {self.email}: {e}")
            logger.error(f"Stored hash format: {repr(self.password[:50])}...")
            return False

    def get_token(self):
        """Generate JWT token"""
        return create_access_token(identity=self.id)
