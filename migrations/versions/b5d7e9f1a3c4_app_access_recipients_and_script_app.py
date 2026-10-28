"""Add access-request notification recipients and script app gating.

Both changes are additive: the new table starts empty (notifications keep
going to all superadmins) and ``script.required_app_key`` is NULL for every
existing script, so existing script access decisions are unchanged.

Revision ID: b5d7e9f1a3c4
Revises: a8d3f1c62b97
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b5d7e9f1a3c4"
down_revision = "a8d3f1c62b97"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_access_notification_recipient",
        sa.Column("app_key", sa.String(length=50), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("added_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "added_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["added_by_user_id"], ["user.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("app_key", "user_id"),
    )
    op.create_index(
        "ix_app_access_notification_recipient_user_id",
        "app_access_notification_recipient",
        ["user_id"],
    )
    op.add_column(
        "script",
        sa.Column("required_app_key", sa.String(length=50), nullable=True),
    )


def downgrade():
    op.drop_column("script", "required_app_key")
    op.drop_index(
        "ix_app_access_notification_recipient_user_id",
        table_name="app_access_notification_recipient",
    )
    op.drop_table("app_access_notification_recipient")
