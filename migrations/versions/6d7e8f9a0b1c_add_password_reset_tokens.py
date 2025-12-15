"""Add password reset tokens table

Revision ID: 6d7e8f9a0b1c
Revises: 5bf3a4279c1d
Create Date: 2025-12-12 00:00:00.000000

Security improvement: Store secure password reset tokens instead of
emailing passwords directly. Tokens expire after 1 hour and can only
be used once.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "6d7e8f9a0b1c"
down_revision = "5bf3a4279c1d"
branch_labels = None
depends_on = None


def upgrade():
    # Create password_reset_token table for secure password recovery
    op.create_table(
        "password_reset_token",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )

    # Create indexes for efficient lookup
    op.create_index(
        "ix_password_reset_token_token",
        "password_reset_token",
        ["token"],
    )
    op.create_index(
        "ix_password_reset_token_user_id",
        "password_reset_token",
        ["user_id"],
    )
    op.create_index(
        "ix_password_reset_token_expires_at",
        "password_reset_token",
        ["expires_at"],
    )


def downgrade():
    # Drop indexes
    op.drop_index("ix_password_reset_token_expires_at", table_name="password_reset_token")
    op.drop_index("ix_password_reset_token_user_id", table_name="password_reset_token")
    op.drop_index("ix_password_reset_token_token", table_name="password_reset_token")

    # Drop table
    op.drop_table("password_reset_token")
