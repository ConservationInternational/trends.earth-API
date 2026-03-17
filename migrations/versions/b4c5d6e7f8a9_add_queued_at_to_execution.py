"""Add queued_at column to execution table for user queue management

Adds queued_at column to track when executions are queued due to
the user having too many concurrent active executions. When queued_at
is set (not NULL), the execution is waiting in a FIFO queue and will
be dispatched when the user's active execution count drops below the
configured limit.

Admin and superadmin users are exempt from queueing.

Revision ID: b4c5d6e7f8a9
Revises: a2b3c4d5e6f7
Create Date: 2026-03-17 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b4c5d6e7f8a9"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade():
    # Add queued_at column to track when an execution was queued
    # NULL means not queued, a timestamp means it's waiting in the queue
    op.add_column(
        "execution",
        sa.Column("queued_at", sa.DateTime(), nullable=True),
    )
    # Add index for efficient queue processing queries
    op.create_index(
        "ix_execution_queued_at",
        "execution",
        ["queued_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_execution_queued_at", table_name="execution")
    op.drop_column("execution", "queued_at")
