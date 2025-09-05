"""Merge all heads for clean migration chain

Revision ID: 79d3f10e7527
Revises: 1a2b3c4d5e6f, 4a7c8b9d2e5f
Create Date: 2025-09-05 20:07:44.440551

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '79d3f10e7527'
down_revision = ('1a2b3c4d5e6f', '4a7c8b9d2e5f')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
