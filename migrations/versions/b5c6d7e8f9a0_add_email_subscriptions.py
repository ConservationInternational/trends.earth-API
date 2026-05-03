"""Add email subscription preferences and bulk_email subscription_type

Adds three user-level subscription columns (news, engagement, system_updates)
and a subscription_type column to bulk_email for category-based filtering.
Existing users default to subscribed (True) for all categories.

Revision ID: b5c6d7e8f9a0
Revises: a3b4c5d6e7f8
Create Date: 2026-05-28
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b5c6d7e8f9a0"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade():
    # User subscription preferences
    op.add_column(
        "user",
        sa.Column(
            "email_subscription_news",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "email_subscription_engagement",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "email_subscription_system_updates",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )

    # Bulk email category for subscription filtering
    op.add_column(
        "bulk_email",
        sa.Column("subscription_type", sa.String(20), nullable=True),
    )


def downgrade():
    op.drop_column("bulk_email", "subscription_type")
    op.drop_column("user", "email_subscription_system_updates")
    op.drop_column("user", "email_subscription_engagement")
    op.drop_column("user", "email_subscription_news")
