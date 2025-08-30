"""Improve status tracking

Revision ID: 20250115_improve_status_tracking
Revises: h34de5fg6789
Create Date: 2025-01-15 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20250115_improve_status_tracking"
down_revision = "h34de5fg6789"
branch_labels = None
depends_on = None


def upgrade():
    """
    Add executions_cancelled column and remove executions_count, users_count, scripts_count
    from status_log table for improved status tracking.
    """
    # Add new executions_cancelled column
    op.add_column('status_log', sa.Column('executions_cancelled', sa.Integer(), nullable=True, default=0))
    
    # Remove the columns that are no longer needed
    with op.batch_alter_table('status_log', schema=None) as batch_op:
        batch_op.drop_column('executions_count')
        batch_op.drop_column('users_count')
        batch_op.drop_column('scripts_count')


def downgrade():
    """
    Reverse the changes: add back executions_count, users_count, scripts_count
    and remove executions_cancelled column.
    """
    # Add back the removed columns
    op.add_column('status_log', sa.Column('executions_count', sa.Integer(), nullable=True, default=0))
    op.add_column('status_log', sa.Column('users_count', sa.Integer(), nullable=True, default=0))
    op.add_column('status_log', sa.Column('scripts_count', sa.Integer(), nullable=True, default=0))
    
    # Remove the new column
    with op.batch_alter_table('status_log', schema=None) as batch_op:
        batch_op.drop_column('executions_cancelled')