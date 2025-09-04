"""Add status transition fields to status_log

Revision ID: 20250904_add_status_transition_fields
Revises: 20250903_replace_active_with_pending_tracking
Create Date: 2025-09-04 21:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250904_add_status_transition_fields"
down_revision = "20250903_replace_active_with_pending_tracking"
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
