"""Add news item translations table

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-03-17 14:00:00.000000

This migration creates the news_item_translation table for storing
translations of news items in multiple languages.

Supported languages: ar, es, fa, fr, pt, ru, sw, zh
(English is stored in the main news_item table)
"""

import sqlalchemy as sa
from alembic import op

from gefapi.models import GUID

# revision identifiers, used by Alembic.
revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "news_item_translation",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("news_item_id", GUID(), nullable=False),
        sa.Column("language_code", sa.String(5), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("link_text", sa.String(100), nullable=True),
        sa.Column("is_machine_translated", sa.Boolean(), nullable=False, default=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["news_item_id"],
            ["news_item.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("news_item_id", "language_code", name="uq_news_translation_lang"),
    )
    # Index for efficient lookup by news_item_id
    op.create_index(
        "ix_news_item_translation_news_item_id",
        "news_item_translation",
        ["news_item_id"],
    )
    # Index for efficient lookup by language
    op.create_index(
        "ix_news_item_translation_language_code",
        "news_item_translation",
        ["language_code"],
    )


def downgrade():
    op.drop_index("ix_news_item_translation_language_code", table_name="news_item_translation")
    op.drop_index("ix_news_item_translation_news_item_id", table_name="news_item_translation")
    op.drop_table("news_item_translation")
