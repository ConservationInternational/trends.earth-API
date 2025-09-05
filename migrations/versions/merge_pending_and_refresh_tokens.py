"""Merge status tracking and refresh tokens branches

Revision ID: 6c8e1f4a7b9d
Revises: 5f6e8a9c1b2d, 9a3b4c5d6e7f
Create Date: 2025-09-03 21:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '6c8e1f4a7b9d'
down_revision = ('5f6e8a9c1b2d', '9a3b4c5d6e7f')
branch_labels = None
depends_on = None

def upgrade():
    pass

def downgrade():
    pass
