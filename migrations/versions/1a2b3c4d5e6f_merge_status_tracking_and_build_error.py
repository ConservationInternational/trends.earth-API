"""Merge status tracking and build error migrations

Revision ID: 1a2b3c4d5e6f
Revises: 8f2e1d0c9b8a, 3eedf39b54dd
Create Date: 2025-09-02 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "1a2b3c4d5e6f"
down_revision = ("8f2e1d0c9b8a", "3eedf39b54dd")  # Multiple parents
branch_labels = None
depends_on = None


def upgrade():
    # This is a merge migration - no schema changes needed
    # Both branches will be merged into this single head
    pass


def downgrade():
    # Cannot downgrade a merge migration
    pass
