"""Add user_client_metadata table for tracking client platform usage.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-03-18 12:00:00.000000

This migration creates the user_client_metadata table for tracking
which client platforms (QGIS plugin, API UI, CLI) users access the
API from, along with version information.

Key features:
- One row per user per client_type (upsert on each access)
- Denormalized columns for fast aggregation queries (os, qgis_version)
- Indexes optimized for stats queries by client_type and time range
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from gefapi.models import GUID

# revision identifiers, used by Alembic.
revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade():
    # Create user_client_metadata table
    op.create_table(
        "user_client_metadata",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("client_type", sa.String(50), nullable=False),
        sa.Column("client_version", sa.String(50), nullable=True),
        sa.Column("os", sa.String(50), nullable=True),
        sa.Column("qgis_version", sa.String(20), nullable=True),
        sa.Column("extra_metadata", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_type", name="uq_user_client_type"),
    )

    # Index for user lookups
    op.create_index(
        "ix_user_client_metadata_user_id",
        "user_client_metadata",
        ["user_id"],
    )

    # Composite index for plugin stats queries (most common)
    op.create_index(
        "ix_client_meta_plugin_stats",
        "user_client_metadata",
        ["client_type", "last_seen_at", "client_version", "qgis_version", "os"],
        postgresql_where=sa.text("client_type = 'qgis_plugin'"),
    )

    # Index for non-plugin stats
    op.create_index(
        "ix_client_meta_other_stats",
        "user_client_metadata",
        ["client_type", "last_seen_at", "client_version"],
        postgresql_where=sa.text("client_type != 'qgis_plugin'"),
    )


def downgrade():
    # Drop indexes first
    op.drop_index("ix_client_meta_other_stats", table_name="user_client_metadata")
    op.drop_index("ix_client_meta_plugin_stats", table_name="user_client_metadata")
    op.drop_index("ix_user_client_metadata_user_id", table_name="user_client_metadata")

    # Drop table
    op.drop_table("user_client_metadata")
