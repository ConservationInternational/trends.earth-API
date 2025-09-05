"""Replace executions_active with executions_pending tracking

Revision ID: 9a3b4c5d6e7f
Revises: 8f2e1d0c9b8a
Create Date: 2025-09-03 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "9a3b4c5d6e7f"
down_revision = "8f2e1d0c9b8a"
branch_labels = None
depends_on = None


def upgrade():
    """
    Replace executions_active with executions_pending for more granular tracking.
    executions_active was a computed field (RUNNING + PENDING), but we want to track
    executions_pending separately to provide better insights.
    """
    # Add new executions_pending column
    op.add_column('status_log', sa.Column('executions_pending', sa.Integer(), nullable=True, default=0))
    
    # Remove the executions_active column
    with op.batch_alter_table('status_log', schema=None) as batch_op:
        batch_op.drop_column('executions_active')


def downgrade():
    """
    Reverse the changes: add back executions_active and remove executions_pending.
    """
    # Add back the executions_active column
    op.add_column('status_log', sa.Column('executions_active', sa.Integer(), nullable=True, default=0))
    
    # Remove the executions_pending column
    with op.batch_alter_table('status_log', schema=None) as batch_op:
        batch_op.drop_column('executions_pending')
