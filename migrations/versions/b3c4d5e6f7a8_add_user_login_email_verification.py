"""add user login and email verification tracking

Revision ID: b3c4d5e6f7a8
Revises: 2c4f8e1a9b3d
Create Date: 2026-02-03 12:00:00.000000

This migration adds fields to track:
- last_login_at: When the user last authenticated
- email_verified: Whether the user has verified their email
- email_verified_at: When the email was verified

These fields support:
1. Login activity tracking for security and analytics
2. Email verification workflow
3. Cleanup tasks for inactive/unverified users

EXISTING USER HANDLING:
All existing users are marked as verified with their created_at timestamp.
For last_login_at, we use the best available proxy for last activity:
1. The end_date of their most recent execution (if they have any)
2. Otherwise, their updated_at timestamp
3. Otherwise, their created_at timestamp
This ensures existing users are "grandfathered in" and won't be affected
by cleanup tasks.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b3c4d5e6f7a8"
down_revision = "2c4f8e1a9b3d"
branch_labels = None
depends_on = None


def upgrade():
    # Add login tracking fields
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_login_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("email_verified", sa.Boolean(), nullable=True))
        batch_op.add_column(
            sa.Column("email_verified_at", sa.DateTime(), nullable=True)
        )

    # Set existing users as verified and set last_login_at based on best available data
    # This is done atomically with the column addition to avoid race conditions
    #
    # For last_login_at, we use (in priority order):
    # 1. MAX(end_date) from their executions - best indicator of actual usage
    # 2. user.updated_at - fallback if no executions
    # 3. user.created_at - final fallback
    op.execute(
        """
        UPDATE "user" u
        SET email_verified = TRUE,
            email_verified_at = u.created_at,
            last_login_at = COALESCE(
                (SELECT MAX(e.end_date) FROM execution e WHERE e.user_id = u.id),
                u.updated_at,
                u.created_at
            )
        WHERE u.email_verified IS NULL
        """
    )

    # Add indexes for efficient queries in cleanup tasks
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.create_index("ix_user_last_login_at", ["last_login_at"], unique=False)
        batch_op.create_index(
            "ix_user_email_verified", ["email_verified"], unique=False
        )


def downgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_index("ix_user_email_verified")
        batch_op.drop_index("ix_user_last_login_at")
        batch_op.drop_column("email_verified_at")
        batch_op.drop_column("email_verified")
        batch_op.drop_column("last_login_at")
