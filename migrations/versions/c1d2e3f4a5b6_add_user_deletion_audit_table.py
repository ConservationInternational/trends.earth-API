"""add user deletion audit table

Revision ID: c1d2e3f4a5b6
Revises: b3c4d5e6f7a8
Create Date: 2026-02-04 12:00:00.000000

This migration creates the user_deletion_audit table for tracking
user account deletions while maintaining GDPR/EUDR compliance.

The table stores:
- Deletion timestamp and reason
- Email hash (for de-duplication detection, not the actual email)
- Non-PII analytics (country, account age, usage statistics)
- Audit trail for compliance reporting

Privacy considerations:
- No actual email addresses are stored
- Email hash is cleared after 30 days for GDPR erasure requests
- Country is retained as aggregate geographic data
- Usage statistics are anonymized counts only
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c1d2e3f4a5b6"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade():
    # Create the user_deletion_audit table
    op.create_table(
        "user_deletion_audit",
        # Primary key
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
        ),
        # When the deletion occurred
        sa.Column("deleted_at", sa.DateTime(), nullable=False),
        # Why the user was deleted
        sa.Column("deletion_reason", sa.String(50), nullable=False),
        # Email hash for de-duplication (SHA-256)
        sa.Column("email_hash", sa.String(64), nullable=True),
        # Non-PII metadata
        sa.Column("country", sa.String(120), nullable=True),
        # Account timestamps
        sa.Column("account_created_at", sa.DateTime(), nullable=True),
        sa.Column("account_age_days", sa.Integer(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("days_since_last_login", sa.Integer(), nullable=True),
        # Usage statistics
        sa.Column("total_executions", sa.Integer(), default=0),
        sa.Column("total_scripts", sa.Integer(), default=0),
        sa.Column("completed_executions", sa.Integer(), default=0),
        sa.Column("failed_executions", sa.Integer(), default=0),
        # Account state at deletion
        sa.Column("email_verified", sa.Boolean(), nullable=True),
        sa.Column("role", sa.String(10), nullable=True),
        # Audit trail
        sa.Column(
            "deleted_by_admin_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("context", sa.Text(), nullable=True),
        # GDPR compliance - when to clear email_hash
        sa.Column("email_hash_expires_at", sa.DateTime(), nullable=True),
    )

    # Create indices for common queries
    op.create_index(
        "ix_user_deletion_audit_deleted_at",
        "user_deletion_audit",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_user_deletion_audit_deletion_reason",
        "user_deletion_audit",
        ["deletion_reason"],
        unique=False,
    )
    op.create_index(
        "ix_user_deletion_audit_email_hash",
        "user_deletion_audit",
        ["email_hash"],
        unique=False,
    )
    # Index for GDPR hash cleanup task
    op.create_index(
        "ix_user_deletion_audit_email_hash_expires",
        "user_deletion_audit",
        ["email_hash_expires_at"],
        unique=False,
    )
    # Index for geographic analytics
    op.create_index(
        "ix_user_deletion_audit_country",
        "user_deletion_audit",
        ["country"],
        unique=False,
    )


def downgrade():
    # Drop indices first
    op.drop_index("ix_user_deletion_audit_country", table_name="user_deletion_audit")
    op.drop_index(
        "ix_user_deletion_audit_email_hash_expires", table_name="user_deletion_audit"
    )
    op.drop_index("ix_user_deletion_audit_email_hash", table_name="user_deletion_audit")
    op.drop_index(
        "ix_user_deletion_audit_deletion_reason", table_name="user_deletion_audit"
    )
    op.drop_index("ix_user_deletion_audit_deleted_at", table_name="user_deletion_audit")
    # Drop the table
    op.drop_table("user_deletion_audit")
