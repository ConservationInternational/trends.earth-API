"""add account lockout fields to user

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-02-05 10:00:00.000000

This migration adds fields to support account lockout after failed login attempts:
- failed_login_count: Number of consecutive failed login attempts
- locked_until: When the account will be automatically unlocked (NULL = not locked)

SECURITY BENEFITS:
1. Prevents slow brute force attacks that bypass rate limiting
2. Forces users with wrong credentials to reset password
3. Reduces Rollbar noise from repeated failed logins

LOCKOUT POLICY:
- After 5 failed attempts: Lock for 15 minutes
- After 10 failed attempts: Lock for 1 hour
- After 20 failed attempts: Lock until password reset
- Successful login or password reset clears the counter

EXISTING USER HANDLING:
All existing users start with failed_login_count = 0 and locked_until = NULL
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "failed_login_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("locked_until", sa.DateTime(), nullable=True)
        )
        # Index for efficient queries on locked accounts
        batch_op.create_index(
            "ix_user_locked_until",
            ["locked_until"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_index("ix_user_locked_until")
        batch_op.drop_column("locked_until")
        batch_op.drop_column("failed_login_count")
