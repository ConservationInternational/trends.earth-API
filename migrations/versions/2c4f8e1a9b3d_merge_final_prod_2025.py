"""Final merge for production deployment 2025

Revision ID: 2c4f8e1a9b3d
Revises: 7b6a9c8d5e4f, merge_merge_pending_refresh
Create Date: 2025-09-05 02:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '2c4f8e1a9b3d'
down_revision = ('7b6a9c8d5e4f', 'a1b2c3d4e5f6')
branch_labels = None
depends_on = None

def upgrade():
    # This is a merge migration - no schema changes needed
    # Both parent migrations have already applied their changes
    pass

def downgrade():
    # This is a merge migration - no schema changes to revert
    pass
