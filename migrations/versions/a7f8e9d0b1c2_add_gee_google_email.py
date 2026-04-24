"""Add gee_google_email to user model

Revision ID: a7f8e9d0b1c2
Revises: a8c2e5f1b3d6
Create Date: 2025-01-23

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7f8e9d0b1c2'
down_revision = 'a8c2e5f1b3d6'
branch_labels = None
depends_on = None


def upgrade():
    """Add gee_google_email column to user table."""
    op.add_column(
        'user',
        sa.Column('gee_google_email', sa.String(length=255), nullable=True)
    )


def downgrade():
    """Remove gee_google_email column from user table."""
    op.drop_column('user', 'gee_google_email')
