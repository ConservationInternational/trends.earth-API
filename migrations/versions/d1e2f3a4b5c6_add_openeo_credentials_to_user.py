"""Add openeo_credentials_enc column to user table.

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-04-23 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d1e2f3a4b5c6"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column("openeo_credentials_enc", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("user", "openeo_credentials_enc")
