"""Add news items table

Revision ID: a2b3c4d5e6f7
Revises: f4a5b6c7d8e9
Create Date: 2026-03-14 10:00:00.000000

This migration creates the news_item table for displaying announcements
and updates to users across different platforms (QGIS plugin, web app, api-ui).

Features:
- News items with title, message, and optional links
- Platform targeting (app, webapp, api-ui)
- Role-based targeting (USER, ADMIN, SUPERADMIN)
- Version range filtering for plugin compatibility
- Priority ordering and news types
"""

import sqlalchemy as sa
from alembic import op

from gefapi.models import GUID

# revision identifiers, used by Alembic.
revision = "a2b3c4d5e6f7"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    """Create news_item table with all columns and indices."""

    # Create news_item table
    op.create_table(
        "news_item",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("link_url", sa.String(500), nullable=True),
        sa.Column("link_text", sa.String(100), nullable=True, default="Learn more"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("publish_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        # Targeting fields
        sa.Column(
            "target_platforms", sa.String(100), nullable=False, default="app,webapp,api-ui"
        ),
        sa.Column("target_roles", sa.String(100), nullable=True, default=None),
        sa.Column("min_version", sa.String(20), nullable=True),
        sa.Column("max_version", sa.String(20), nullable=True),
        # Status and display
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("priority", sa.Integer(), nullable=False, default=0),
        sa.Column("news_type", sa.String(20), nullable=False, default="info"),
        # Tracking
        sa.Column("created_by_id", GUID(), sa.ForeignKey("user.id"), nullable=True),
    )

    # Create indices for common queries (API filtering and sorting)
    op.create_index("ix_news_item_publish_at", "news_item", ["publish_at"])
    op.create_index("ix_news_item_expires_at", "news_item", ["expires_at"])
    op.create_index("ix_news_item_is_active", "news_item", ["is_active"])
    op.create_index("ix_news_item_priority", "news_item", ["priority"])
    op.create_index("ix_news_item_news_type", "news_item", ["news_type"])
    op.create_index("ix_news_item_created_by_id", "news_item", ["created_by_id"])
    # Composite index for common query pattern: active + published + not expired
    op.create_index(
        "ix_news_item_active_published",
        "news_item",
        ["is_active", "publish_at", "expires_at"],
    )


def downgrade():
    """Remove news_item table."""
    op.drop_table("news_item")
