"""Expiring public web-map shares."""

import datetime
import uuid

from gefapi import db
from gefapi.models import GUID

db.GUID = GUID


def utcnow():
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


class PublicShare(db.Model):
    """A bearer-authorized, expiring snapshot of locally generated GIS layers."""

    __tablename__ = "public_share"

    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,
    )
    user_id = db.Column(
        db.GUID(),
        db.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title = db.Column(db.String(120), nullable=False)
    mask = db.Column(db.JSON(), nullable=True)
    mask_version = db.Column(db.Integer(), nullable=False, default=0)
    retention_days = db.Column(db.Integer(), nullable=False)
    state = db.Column(db.String(20), nullable=False, default="draft", index=True)
    update_state = db.Column(db.String(20), nullable=False, default="idle")
    created_at = db.Column(db.DateTime(), nullable=False, default=utcnow)
    ready_at = db.Column(db.DateTime(), nullable=True)
    expires_at = db.Column(db.DateTime(), nullable=True, index=True)
    revoked_at = db.Column(db.DateTime(), nullable=True)
    token_hash = db.Column(db.String(64), nullable=True, index=True)
    # Fernet ciphertext of the current capability so the owner can view the link.
    link_ciphertext = db.Column(db.Text(), nullable=True)
    session_generation = db.Column(db.Integer(), nullable=False, default=0)
    quota_reserved_bytes = db.Column(db.BigInteger(), nullable=False, default=0)
    cleanup_completed_at = db.Column(db.DateTime(), nullable=True)
    cleanup_error = db.Column(db.String(500), nullable=True)
    owner = db.relationship("User", backref=db.backref("public_shares", lazy="dynamic"))
    layers = db.relationship(
        "PublicShareLayer",
        back_populates="share",
        cascade="all, delete-orphan",
        order_by="(PublicShareLayer.position, PublicShareLayer.created_at)",
    )

    def is_accessible(self, now=None):
        """Return whether a published share currently grants public access."""
        now = now or utcnow()
        return (
            self.state == "ready"
            and self.ready_at is not None
            and self.revoked_at is None
            and self.expires_at is not None
            and self.expires_at > now
            and self.cleanup_completed_at is None
        )

    def serialize(self):
        """Return owner-visible status without secrets or storage credentials."""
        return {
            "id": str(self.id),
            "title": self.title,
            "has_mask": self.mask is not None,
            "mask_version": self.mask_version,
            "state": self.state,
            "update_state": self.update_state,
            "retention_days": self.retention_days,
            "created_at": self.created_at.isoformat() + "Z",
            "ready_at": self.ready_at.isoformat() + "Z" if self.ready_at else None,
            "expires_at": (
                self.expires_at.isoformat() + "Z" if self.expires_at else None
            ),
            "revoked_at": (
                self.revoked_at.isoformat() + "Z" if self.revoked_at else None
            ),
            "quota_reserved_bytes": self.quota_reserved_bytes,
            "validation_errors": [
                {"layer_id": str(layer.id), "error": layer.validation_error}
                for layer in self.layers
                if layer.validation_error
            ],
            "layers": [
                layer.serialize() for layer in self.layers if layer.removed_at is None
            ],
        }


class PublicShareLayer(db.Model):
    """One declared GIS layer and its immutable private object in a share."""

    __tablename__ = "public_share_layer"

    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,
    )
    share_id = db.Column(
        db.GUID(),
        db.ForeignKey("public_share.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(120), nullable=False)
    kind = db.Column(db.String(16), nullable=False, default="raster")
    band = db.Column(db.Integer(), nullable=True)
    vector_metadata = db.Column(db.JSON(), nullable=True)
    expected_size_bytes = db.Column(db.BigInteger(), nullable=False)
    expected_sha256 = db.Column(db.String(64), nullable=False)
    object_key = db.Column(db.String(512), nullable=False, unique=True)
    object_version_id = db.Column(db.String(256), nullable=True)
    checksum_sha256 = db.Column(db.String(64), nullable=True)
    style = db.Column(db.JSON(), nullable=False)
    attribution = db.Column(db.String(500), nullable=False)
    bounds = db.Column(db.JSON(), nullable=True)
    crs = db.Column(db.Text(), nullable=True)
    nodata = db.Column(db.JSON(), nullable=True)
    raster_band_count = db.Column(db.Integer(), nullable=True)
    upload_id = db.Column(db.String(1024), nullable=True)
    upload_part_size = db.Column(db.Integer(), nullable=True)
    upload_part_count = db.Column(db.Integer(), nullable=True)
    upload_completed = db.Column(db.Boolean(), nullable=False, default=False)
    published = db.Column(db.Boolean(), nullable=False, default=False)
    removed_at = db.Column(db.DateTime(), nullable=True, index=True)
    # Display order in the viewer; the lowest position is the top layer.
    position = db.Column(db.Integer(), nullable=False, default=0)
    validation_error = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime(), nullable=False, default=utcnow)

    share = db.relationship("PublicShare", back_populates="layers")

    def serialize(self):
        """Return safe owner-visible layer status, never private object details."""
        return {
            "id": str(self.id),
            "name": self.name,
            "kind": self.kind,
            "band": self.band,
            "vector_metadata": self.vector_metadata,
            "size_bytes": self.expected_size_bytes,
            "checksum_sha256": self.checksum_sha256,
            "style": self.style,
            "attribution": self.attribution,
            "bounds": self.bounds,
            "crs": self.crs,
            "nodata": self.nodata,
            "raster_band_count": self.raster_band_count,
            "upload_completed": self.upload_completed,
            "published": self.published,
        }
