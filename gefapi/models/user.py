"""USER MODEL"""

import base64
import datetime
import json
import logging
import os
from typing import Any, Optional
import uuid

from cryptography.fernet import Fernet
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
    # Session management refresh tokens
    user_refresh_tokens = db.relationship(
        "RefreshToken",
        cascade="all, delete-orphan",
        lazy="dynamic",
        back_populates="user",
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

    # Google Earth Engine credentials fields
    gee_oauth_token = db.Column(db.Text(), nullable=True)
    gee_refresh_token = db.Column(db.Text(), nullable=True)
    gee_service_account_key = db.Column(db.Text(), nullable=True)
    # 'oauth' or 'service_account'
    gee_credentials_type = db.Column(db.String(20), nullable=True)
    gee_credentials_created_at = db.Column(db.DateTime(), nullable=True)

    # Email notification preferences
    email_notifications_enabled = db.Column(db.Boolean(), default=True, nullable=False)

    def __init__(self, email, password, name, country, institution, role="USER"):
        self.email = email
        self.password = self.set_password(password)
        self.role = role if role in ["USER", "ADMIN", "SUPERADMIN"] else "USER"
        self.name = name
        self.country = country
        self.institution = institution
        # Ensure email_notifications_enabled gets the default value
        self.email_notifications_enabled = True

    def __repr__(self):
        return f"<User {self.email!r}>"

    def serialize(self, include=None, exclude=None):
        """Return object data in easily serializeable format

        Args:
            include (list, optional): List of additional fields to include
                (e.g., 'google_groups', 'scripts')
            exclude (list, optional): List of fields to exclude from serialization

        Returns:
            dict: User object serialized as dictionary including:
                - Basic user fields: id, email, name, country, institution, role
                - Timestamps: created_at, updated_at
                - Preferences: email_notifications_enabled
                - Optional fields based on include parameter
        """
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

        # Include GEE credentials status if requested
        if "gee_credentials" in include:
            user["gee_credentials"] = {
                "has_credentials": self.has_gee_credentials(),
                "credentials_type": self.gee_credentials_type,
                "created_at": self.gee_credentials_created_at.isoformat()
                if self.gee_credentials_created_at
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

    @staticmethod
    def _get_encryption_key() -> bytes:
        """Get encryption key for GEE credentials"""
        key = os.getenv("GEE_ENCRYPTION_KEY") or os.getenv(
            "SECRET_KEY", "default-key-change-in-production"
        )
        # Ensure key is 32 bytes for Fernet
        key_bytes = key.encode("utf-8")[:32].ljust(32, b"0")
        return base64.urlsafe_b64encode(key_bytes)

    def _encrypt_gee_data(self, data: str) -> str:
        """Encrypt GEE credential data"""
        if not data:
            return None
        fernet = Fernet(self._get_encryption_key())
        encrypted = fernet.encrypt(data.encode("utf-8"))
        return base64.b64encode(encrypted).decode("utf-8")

    def _decrypt_gee_data(self, encrypted_data: str) -> str:
        """Decrypt GEE credential data"""
        if not encrypted_data:
            return None
        try:
            fernet = Fernet(self._get_encryption_key())
            decoded = base64.b64decode(encrypted_data.encode("utf-8"))
            return fernet.decrypt(decoded).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to decrypt GEE data for user {self.email}: {e}")
            return None

    def set_gee_oauth_credentials(self, access_token: str, refresh_token: str) -> None:
        """Set OAuth credentials for GEE"""
        self.gee_oauth_token = self._encrypt_gee_data(access_token)
        self.gee_refresh_token = self._encrypt_gee_data(refresh_token)
        self.gee_credentials_type = "oauth"
        self.gee_credentials_created_at = datetime.datetime.utcnow()

    def set_gee_service_account(self, service_account_key: dict[str, Any]) -> None:
        """Set service account credentials for GEE"""
        self.gee_service_account_key = self._encrypt_gee_data(
            json.dumps(service_account_key)
        )
        self.gee_credentials_type = "service_account"
        self.gee_credentials_created_at = datetime.datetime.utcnow()

    def get_gee_oauth_credentials(self) -> tuple[Optional[str], Optional[str]]:
        """Get OAuth credentials for GEE"""
        if self.gee_credentials_type != "oauth":
            return None, None
        access_token = self._decrypt_gee_data(self.gee_oauth_token)
        refresh_token = self._decrypt_gee_data(self.gee_refresh_token)
        return access_token, refresh_token

    def get_gee_service_account(self) -> Optional[dict[str, Any]]:
        """Get service account credentials for GEE"""
        if self.gee_credentials_type != "service_account":
            return None
        key_data = self._decrypt_gee_data(self.gee_service_account_key)
        if key_data:
            try:
                return json.loads(key_data)
            except json.JSONDecodeError as e:
                logger.error(
                    f"Failed to parse service account key for user {self.email}: {e}"
                )
        return None

    def has_gee_credentials(self) -> bool:
        """Check if user has any GEE credentials configured"""
        return self.gee_credentials_type is not None

    def clear_gee_credentials(self) -> None:
        """Clear all GEE credentials"""
        self.gee_oauth_token = None
        self.gee_refresh_token = None
        self.gee_service_account_key = None
        self.gee_credentials_type = None
        self.gee_credentials_created_at = None
