"""Add bulk email tables

Adds three tables for the Bulk Email feature:
  - bulk_email_recipient_list: named groups with JSON filter criteria
  - bulk_email: draft and sent bulk emails with HTML content
  - bulk_email_verification_token: 6-digit OTP for confirming large sends

Revision ID: a3b4c5d6e7f8
Revises: c0d1e2f3a4b5
Create Date: 2026-05-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "a3b4c5d6e7f8"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade():
    """Create bulk email tables."""
    op.create_table(
        "bulk_email_recipient_list",
        sa.Column("id", sa.String(32), primary_key=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("filter_criteria", sa.JSON(), nullable=False),
        sa.Column("estimated_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "bulk_email",
        sa.Column("id", sa.String(32), primary_key=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("html_content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="DRAFT"),
        sa.Column(
            "recipient_list_id",
            sa.String(32),
            sa.ForeignKey("bulk_email_recipient_list.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recipient_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=False,
        ),
        sa.Column(
            "sent_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "bulk_email_verification_token",
        sa.Column("id", sa.String(32), primary_key=True, nullable=False),
        sa.Column("token", sa.String(6), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "bulk_email_id",
            sa.String(32),
            sa.ForeignKey("bulk_email.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    """Drop bulk email tables."""
    op.drop_table("bulk_email_verification_token")
    op.drop_table("bulk_email")
    op.drop_table("bulk_email_recipient_list")
