"""Make news/engagement email subscriptions opt-in (system_updates stays opt-out).

Only changes the column server_default for new rows going forward; existing
users' current preference values are left untouched.

Revision ID: 0fa1182925f5
Revises: e4024f260d06
Create Date: 2026-08-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0fa1182925f5"
down_revision = "e4024f260d06"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "user",
        "email_subscription_news",
        server_default=sa.false(),
    )
    op.alter_column(
        "user",
        "email_subscription_engagement",
        server_default=sa.false(),
    )


def downgrade():
    op.alter_column(
        "user",
        "email_subscription_news",
        server_default=sa.true(),
    )
    op.alter_column(
        "user",
        "email_subscription_engagement",
        server_default=sa.true(),
    )
