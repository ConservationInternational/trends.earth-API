"""Add expires_at to rate_limit_event

Revision ID: 5bf3a4279c1d
Revises: 9c02502c27db
Create Date: 2025-11-02 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5bf3a4279c1d"
down_revision = "2b1a0f5c3e9d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "rate_limit_event",
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_rate_limit_event_expires_at",
        "rate_limit_event",
        ["expires_at"],
    )
    op.execute(
        sa.text(
            "UPDATE rate_limit_event SET expires_at = occurred_at WHERE expires_at IS NULL"
        )
    )


def downgrade():
    op.execute(
        sa.text(
            "UPDATE rate_limit_event SET expires_at = NULL"
        )
    )
    op.drop_index("ix_rate_limit_event_expires_at", table_name="rate_limit_event")
    op.drop_column("rate_limit_event", "expires_at")
