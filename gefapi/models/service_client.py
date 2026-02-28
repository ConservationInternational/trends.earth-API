"""SERVICE CLIENT MODEL

OAuth2 service clients for external service-to-service authentication
using the Client Credentials grant (RFC 6749 §4.4).

Each service client is associated with a user and inherits that user's
permissions.  The client secret is stored as a salted SHA-256 hash — the
raw secret is shown exactly once at creation time and cannot be retrieved
later.
"""

import datetime
import hashlib
import logging
import secrets
import uuid

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID

logger = logging.getLogger(__name__)

CLIENT_ID_BYTE_LENGTH = 16  # 128-bit client_id
CLIENT_SECRET_BYTE_LENGTH = 32  # 256-bit secret
CLIENT_ID_PREFIX = "te_cid_"
CLIENT_SECRET_PREFIX = "te_cs_"


class ServiceClient(db.Model):
    """OAuth2 service client for the Client Credentials grant."""

    __tablename__ = "service_client"

    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,
    )
    # Human-readable label chosen by the user
    name = db.Column(db.String(120), nullable=False)
    # Deterministic client_id exposed to callers (not secret)
    client_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    # SHA-256 hash of the full raw client_secret
    client_secret_hash = db.Column(db.String(64), nullable=False)
    # First 8 hex chars after the prefix so users can identify secrets
    secret_prefix = db.Column(db.String(16), nullable=False)
    # Space-delimited OAuth2 scopes (empty string = full access)
    scopes = db.Column(db.Text, nullable=False, default="")
    # Owner – the user whose permissions this client inherits
    user_id = db.Column(db.GUID(), db.ForeignKey("user.id"), nullable=False, index=True)
    created_at = db.Column(
        db.DateTime(), default=lambda: datetime.datetime.now(datetime.UTC)
    )
    last_used_at = db.Column(db.DateTime(), nullable=True)
    expires_at = db.Column(db.DateTime(), nullable=True)
    revoked = db.Column(db.Boolean(), default=False, nullable=False)

    user = db.relationship(
        "User", backref=db.backref("service_clients", lazy="dynamic")
    )

    # ------------------------------------------------------------------
    # Class helpers
    # ------------------------------------------------------------------

    @classmethod
    def generate_credentials(cls):
        """Return ``(client_id, raw_secret, secret_hash)``.

        * ``client_id`` is a non-secret identifier sent in plain text.
        * ``raw_secret`` is the one-time secret shown to the user.
        * ``secret_hash`` is the SHA-256 stored in the database.
        """
        cid = f"{CLIENT_ID_PREFIX}{secrets.token_hex(CLIENT_ID_BYTE_LENGTH)}"
        raw_secret = (
            f"{CLIENT_SECRET_PREFIX}{secrets.token_hex(CLIENT_SECRET_BYTE_LENGTH)}"
        )
        secret_hash = hashlib.sha256(raw_secret.encode()).hexdigest()
        return cid, raw_secret, secret_hash

    @classmethod
    def hash_secret(cls, raw_secret: str) -> str:
        """Hash a raw client secret for comparison."""
        return hashlib.sha256(raw_secret.encode()).hexdigest()

    @classmethod
    def lookup(cls, client_id: str):
        """Find a ServiceClient by its public ``client_id``."""
        return cls.query.filter_by(client_id=client_id).first()

    # ------------------------------------------------------------------
    # Instance helpers
    # ------------------------------------------------------------------

    def verify_secret(self, raw_secret: str) -> bool:
        """Return ``True`` if *raw_secret* matches the stored hash."""
        return self.client_secret_hash == self.hash_secret(raw_secret)

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.datetime.now(datetime.UTC) > self.expires_at.replace(
            tzinfo=datetime.UTC
        )

    def is_valid(self) -> bool:
        return not self.revoked and not self.is_expired()

    TOUCH_THROTTLE_SECONDS = 300

    def touch(self):
        now = datetime.datetime.now(datetime.UTC)
        if self.last_used_at is not None:
            last = self.last_used_at.replace(tzinfo=datetime.UTC)
            if (now - last).total_seconds() < self.TOUCH_THROTTLE_SECONDS:
                return
        self.last_used_at = now

    def serialize(self):
        return {
            "id": self.id,
            "name": self.name,
            "client_id": self.client_id,
            "secret_prefix": self.secret_prefix,
            "scopes": self.scopes,
            "user_id": self.user_id,
            "created_at": (self.created_at.isoformat() if self.created_at else None),
            "last_used_at": (
                self.last_used_at.isoformat() if self.last_used_at else None
            ),
            "expires_at": (self.expires_at.isoformat() if self.expires_at else None),
            "revoked": self.revoked,
        }
