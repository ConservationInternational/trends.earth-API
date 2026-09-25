"""Persistent records used by the Trends.Earth OpenID Connect provider."""

import datetime
import hashlib
from urllib.parse import urlsplit
import uuid

from gefapi import db
from gefapi.models import GUID

# RFC 8252 §7.3 native-app redirect URIs: the port is assigned at runtime, so a
# registered loopback URI matches any port.  Only these literal hosts qualify.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


class OAuthClient(db.Model):
    """An OAuth client registered for an application."""

    __tablename__ = "oauth_clients"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id = db.Column(db.String(120), unique=True, nullable=False, index=True)
    client_secret_hash = db.Column(db.String(256), nullable=True)
    name = db.Column(db.String(120), nullable=False)
    redirect_uris = db.Column(db.Text, nullable=False)
    post_logout_redirect_uris = db.Column(db.Text, nullable=False, default="")
    scopes = db.Column(db.String(255), nullable=False, default="openid email profile")
    audience = db.Column(db.String(255), unique=True, nullable=False)
    is_public = db.Column(db.Boolean, nullable=False, default=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # NULL means the client is ungated.  Non-null requires an active
    # user_app_access grant for that application key.
    required_app_key = db.Column(db.String(50), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.UTC)
    )

    def allowed_redirect_uris(self):
        return [uri for uri in self.redirect_uris.split("\n") if uri]

    def allowed_post_logout_redirect_uris(self):
        return [uri for uri in self.post_logout_redirect_uris.split("\n") if uri]

    def matches_redirect_uri(self, uri):
        """Return True when *uri* is permitted for this client.

        Exact match is required, except for registered loopback URIs, where the
        port is wildcarded per RFC 8252 §7.3 so that a native application can
        bind an ephemeral port.  Scheme, host, and path must still match
        exactly, and only literal loopback hosts qualify — never ``localhost``.
        """
        if not uri:
            return False
        registered = self.allowed_redirect_uris()
        if uri in registered:
            return True
        try:
            candidate = urlsplit(uri)
        except ValueError:
            return False
        if _loopback_host(candidate) is None:
            return False
        for entry in registered:
            try:
                allowed = urlsplit(entry)
            except ValueError:
                continue
            if _loopback_host(allowed) is None:
                continue
            if (
                candidate.scheme == allowed.scheme
                and _loopback_host(candidate) == _loopback_host(allowed)
                and candidate.path == allowed.path
                and candidate.query == allowed.query
            ):
                return True
        return False

    def has_scope(self, scope):
        return scope in self.scopes.split()

    def verify_secret(self, secret):
        if self.is_public or not self.client_secret_hash:
            return False
        from werkzeug.security import check_password_hash

        return check_password_hash(self.client_secret_hash, secret)

    @staticmethod
    def hash_code(value):
        return hashlib.sha256(value.encode()).hexdigest()


def _loopback_host(parts):
    """Return the bare loopback host of a parsed URL, or ``None``."""
    hostname = parts.hostname
    if hostname is None:
        return None
    return hostname if hostname in LOOPBACK_HOSTS else None


class AuthorizationCode(db.Model):
    """Single-use authorization code bound to redirect URI and PKCE verifier."""

    __tablename__ = "authorization_codes"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    code_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    client_id = db.Column(db.String(120), nullable=False, index=True)
    user_id = db.Column(GUID(), db.ForeignKey("user.id"), nullable=False, index=True)
    redirect_uri = db.Column(db.Text, nullable=False)
    code_challenge = db.Column(db.String(128), nullable=False)
    code_challenge_method = db.Column(db.String(10), nullable=False, default="S256")
    scope = db.Column(db.String(255), nullable=False)
    nonce = db.Column(db.String(255), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    used_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User")

    def is_valid(self):
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        expires_at = self.expires_at.replace(tzinfo=None)
        return self.used_at is None and expires_at > now


class OIDCRefreshToken(db.Model):
    """Hashed, client-bound refresh tokens issued by the OIDC provider."""

    __tablename__ = "oidc_refresh_tokens"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4, nullable=False)
    user_id = db.Column(GUID(), db.ForeignKey("user.id"), nullable=False, index=True)
    client_id = db.Column(db.String(120), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    family_id = db.Column(GUID(), nullable=False, default=uuid.uuid4, index=True)
    scope = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )
    is_revoked = db.Column(db.Boolean, default=False, nullable=False)

    user = db.relationship("User")

    def is_valid(self):
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        expires_at = self.expires_at.replace(tzinfo=None)
        return not self.is_revoked and expires_at > now

    def revoke(self):
        self.is_revoked = True
