"""Add per-user public-share storage quota overrides.

Revision ID: 8c4f2a9d1e73
Revises: 7d4a2e8c1f60
Create Date: 2026-11-21
"""

from alembic import op
import sqlalchemy as sa

revision = "8c4f2a9d1e73"
down_revision = "7d4a2e8c1f60"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column("public_share_quota_bytes", sa.BigInteger(), nullable=True),
    )


def downgrade():
    op.drop_column("user", "public_share_quota_bytes")
