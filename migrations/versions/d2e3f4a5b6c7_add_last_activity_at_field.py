"""add last_activity_at field to user

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-02-04 14:00:00.000000

This migration adds the last_activity_at field to track when a user
last interacted with the system (via login OR token refresh).

This provides a better proxy for "active users" than last_login_at alone,
since users may stay logged in for weeks using token refresh without
re-entering credentials.

EXISTING USER HANDLING:
- For users with last_login_at set: use that value
- For users without last_login_at: use MAX of refresh token last_used_at,
  updated_at, or created_at
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade():
    # Add last_activity_at column to user table
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_activity_at", sa.DateTime(), nullable=True))

    # Populate last_activity_at for existing users using best available data:
    # 1. If they have refresh tokens, use MAX(last_used_at) from those
    # 2. Otherwise, use last_login_at if available
    # 3. Otherwise, use updated_at or created_at
    op.execute(
        """
        UPDATE "user" u
        SET last_activity_at = COALESCE(
            (SELECT MAX(rt.last_used_at) FROM refresh_tokens rt WHERE rt.user_id = u.id),
            u.last_login_at,
            u.updated_at,
            u.created_at
        )
        """
    )

    # Add index for efficient queries on last_activity_at
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.create_index(
            "ix_user_last_activity_at", ["last_activity_at"], unique=False
        )

    # Add last_activity_at columns to user_deletion_audit table
    with op.batch_alter_table("user_deletion_audit", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_activity_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("days_since_last_activity", sa.Integer(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("user_deletion_audit", schema=None) as batch_op:
        batch_op.drop_column("days_since_last_activity")
        batch_op.drop_column("last_activity_at")

    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_index("ix_user_last_activity_at")
        batch_op.drop_column("last_activity_at")
