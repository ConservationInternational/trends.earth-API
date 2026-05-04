"""Add fields_data column to bulk_email table.

Revision ID: d49638469453
Revises: b5c6d7e8f9a0
Create Date: 2025-05-04 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d49638469453"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bulk_email", sa.Column("fields_data", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("bulk_email", "fields_data")
