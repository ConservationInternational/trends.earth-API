"""Add expiring public web-map shares.

Revision ID: 7d4a2e8c1f60
Revises: 6f3a90c2d8e1
Create Date: 2026-11-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "7d4a2e8c1f60"
down_revision = "6f3a90c2d8e1"
branch_labels = None
depends_on = None


def _uuid_type():
    return postgresql.UUID(as_uuid=False)


def upgrade():
    op.create_table(
        "public_share",
        sa.Column("id", _uuid_type(), nullable=False),
        sa.Column("user_id", _uuid_type(), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("ready_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("session_generation", sa.Integer(), nullable=False),
        sa.Column("quota_reserved_bytes", sa.BigInteger(), nullable=False),
        sa.Column("cleanup_completed_at", sa.DateTime(), nullable=True),
        sa.Column("cleanup_error", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_public_share_user_id", "public_share", ["user_id"])
    op.create_index("ix_public_share_state", "public_share", ["state"])
    op.create_index("ix_public_share_expires_at", "public_share", ["expires_at"])

    op.create_table(
        "public_share_layer",
        sa.Column("id", _uuid_type(), nullable=False),
        sa.Column("share_id", _uuid_type(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("band", sa.Integer(), nullable=False),
        sa.Column("expected_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expected_sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("object_version_id", sa.String(length=256), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("style", sa.JSON(), nullable=False),
        sa.Column("attribution", sa.String(length=500), nullable=False),
        sa.Column("bounds", sa.JSON(), nullable=True),
        sa.Column("crs", sa.Text(), nullable=True),
        sa.Column("nodata", sa.JSON(), nullable=True),
        sa.Column("raster_band_count", sa.Integer(), nullable=True),
        sa.Column("upload_id", sa.String(length=1024), nullable=True),
        sa.Column("upload_part_size", sa.Integer(), nullable=True),
        sa.Column("upload_part_count", sa.Integer(), nullable=True),
        sa.Column("upload_completed", sa.Boolean(), nullable=False),
        sa.Column("validation_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["share_id"], ["public_share.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(
        "ix_public_share_layer_share_id", "public_share_layer", ["share_id"]
    )


def downgrade():
    op.drop_index("ix_public_share_layer_share_id", table_name="public_share_layer")
    op.drop_table("public_share_layer")
    op.drop_index("ix_public_share_expires_at", table_name="public_share")
    op.drop_index("ix_public_share_state", table_name="public_share")
    op.drop_index("ix_public_share_user_id", table_name="public_share")
    op.drop_table("public_share")
