"""Add status transition fields to status_log

Revision ID: 4a7c8b9d2e5f
Revises: 2c4f8e1a9b3d
Create Date: 2025-09-04 21:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "4a7c8b9d2e5f"
down_revision = "2c4f8e1a9b3d"
branch_labels = None
depends_on = None


def upgrade():
    """
    Add status transition fields to status_log table:
    - status_from: the previous status
    - status_to: the new status
    - execution_id: which execution is changing status
    """
    # Add status transition fields
    op.add_column('status_log', sa.Column('status_from', sa.String(20), nullable=True))
    op.add_column('status_log', sa.Column('status_to', sa.String(20), nullable=True))
    # Use GUID type for execution_id to match the Execution.id type
    op.add_column('status_log', sa.Column('execution_id', sa.String(36), nullable=True))


def downgrade():
    """
    Remove the status transition fields.
    """
    with op.batch_alter_table('status_log', schema=None) as batch_op:
        batch_op.drop_column('execution_id')
        batch_op.drop_column('status_to')
        batch_op.drop_column('status_from')
