"""Add consent_given_at and consent_source columns to user.

Revision ID: e4024f260d06
Revises: d49638469453
Create Date: 2026-08-12 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e4024f260d06"
down_revision = "d49638469453"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user", sa.Column("consent_given_at", sa.DateTime(), nullable=True))
    op.add_column(
        "user", sa.Column("consent_source", sa.String(length=50), nullable=True)
    )


def downgrade():
    op.drop_column("user", "consent_source")
    op.drop_column("user", "consent_given_at")
