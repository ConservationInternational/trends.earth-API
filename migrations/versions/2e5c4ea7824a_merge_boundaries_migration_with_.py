"""merge boundaries migration with existing head

Revision ID: 2e5c4ea7824a
Revises: 79d3f10e7527, 8a9b0c1d2e3f
Create Date: 2025-10-23 18:01:56.389299

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2e5c4ea7824a'
down_revision = ('79d3f10e7527', '8a9b0c1d2e3f')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
