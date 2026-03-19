"""Add language column to user_client_metadata table

Revision ID: b2d4f6a8c0e1
Revises: a1b3c5d7e9f2
Create Date: 2026-03-19 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "b2d4f6a8c0e1"
down_revision = "a1b3c5d7e9f2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_client_metadata",
        sa.Column("language", sa.String(10), nullable=True),
    )


def downgrade():
    op.drop_column("user_client_metadata", "language")
