"""Merge status tracking and refresh tokens branches

Revision ID: merge_merge_pending_refresh
Revises: add_refresh_tokens, 9a3b4c5d6e7f
Create Date: 2025-09-03 21:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'merge_merge_pending_refresh'
down_revision = ('add_refresh_tokens', '9a3b4c5d6e7f')
branch_labels = None
depends_on = None

def upgrade():
    pass

def downgrade():
    pass
