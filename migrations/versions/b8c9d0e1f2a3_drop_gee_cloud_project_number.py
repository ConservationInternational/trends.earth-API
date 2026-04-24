"""Drop gee_cloud_project_number column

Revision ID: b8c9d0e1f2a3
Revises: a7f8e9d0b1c2
Create Date: 2025-01-23

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8c9d0e1f2a3'
down_revision = 'a7f8e9d0b1c2'
branch_labels = None
depends_on = None


def upgrade():
    """Drop gee_cloud_project_number column (no longer needed with user-based bucket access)."""
    op.drop_column('user', 'gee_cloud_project_number')


def downgrade():
    """Re-add gee_cloud_project_number column."""
    op.add_column(
        'user',
        sa.Column('gee_cloud_project_number', sa.BigInteger(), nullable=True)
    )
