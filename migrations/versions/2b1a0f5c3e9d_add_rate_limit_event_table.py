"""Add table for tracking rate limit events

Revision ID: 2b1a0f5c3e9d
Revises: 2e5c4ea7824a
Create Date: 2025-11-01 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

from gefapi.models import GUID


# revision identifiers, used by Alembic.
revision = "2b1a0f5c3e9d"
down_revision = "2e5c4ea7824a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rate_limit_event",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", GUID(), nullable=True),
        sa.Column("user_role", sa.String(length=20), nullable=True),
        sa.Column("user_email", sa.String(length=255), nullable=True),
        sa.Column("rate_limit_type", sa.String(length=50), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=True),
        sa.Column("limit_definition", sa.String(length=120), nullable=True),
        sa.Column("limit_count", sa.Integer(), nullable=True),
        sa.Column("time_window_seconds", sa.Integer(), nullable=True),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.Column("limit_key", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_rate_limit_event_user"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_rate_limit_event_occurred_at",
        "rate_limit_event",
        ["occurred_at"],
    )
    op.create_index(
        "ix_rate_limit_event_user_id",
        "rate_limit_event",
        ["user_id"],
    )
    op.create_index(
        "ix_rate_limit_event_type",
        "rate_limit_event",
        ["rate_limit_type"],
    )
    op.create_index(
        "ix_rate_limit_event_ip",
        "rate_limit_event",
        ["ip_address"],
    )
    op.create_index(
        "ix_rate_limit_event_limit_key",
        "rate_limit_event",
        ["limit_key"],
    )


def downgrade():
    op.drop_index("ix_rate_limit_event_ip", table_name="rate_limit_event")
    op.drop_index("ix_rate_limit_event_limit_key", table_name="rate_limit_event")
    op.drop_index("ix_rate_limit_event_type", table_name="rate_limit_event")
    op.drop_index("ix_rate_limit_event_user_id", table_name="rate_limit_event")
    op.drop_index("ix_rate_limit_event_occurred_at", table_name="rate_limit_event")
    op.drop_table("rate_limit_event")
