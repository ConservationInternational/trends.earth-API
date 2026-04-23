"""Add openeo_backend_url column to script table.

Revision ID: c7d8e9f0a1b2
Revises: b2d4f6a8c0e1
Create Date: 2026-04-23 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c7d8e9f0a1b2"
down_revision = "b2d4f6a8c0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "script",
        sa.Column("openeo_backend_url", sa.String(512), nullable=True),
    )


def downgrade():
    op.drop_column("script", "openeo_backend_url")
