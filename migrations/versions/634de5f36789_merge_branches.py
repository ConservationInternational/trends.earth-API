"""Merge branches: status_log and script enhancements

Revision ID: 234de5f6789a
Revises: 123bc4de5678, 115924e98eb5
Create Date: 2025-07-09 15:50:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "634de5f36789"
down_revision = ("923bc4de5678", "115924e98eb5")  # Multiple parents
branch_labels = None
depends_on = None


def upgrade():
    # This is a merge migration - no schema changes needed
    # Both branches will be merged into this single head
    pass


def downgrade():
    # Cannot downgrade a merge migration
    pass
