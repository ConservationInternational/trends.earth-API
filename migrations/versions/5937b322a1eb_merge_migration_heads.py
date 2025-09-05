"""Merge migration heads

Revision ID: 5937b322a1eb
Revises: add_refresh_tokens, h34de5fg6789
Create Date: 2025-07-29 15:02:03.636891

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5937b322a1eb'
down_revision = ('8f9a0b1c2d3e', 'h34de5fg6789')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
