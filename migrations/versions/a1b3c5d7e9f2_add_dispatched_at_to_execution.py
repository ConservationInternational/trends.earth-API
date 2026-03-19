"""Add dispatched_at column to execution table

Tracks when the docker_run Celery task starts processing an execution.
Used by monitor_failed_docker_services to enforce a grace period and
avoid killing executions before their Docker service has been created.
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b3c5d7e9f2"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "execution",
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        op.f("ix_execution_dispatched_at"),
        "execution",
        ["dispatched_at"],
    )


def downgrade():
    op.drop_index(op.f("ix_execution_dispatched_at"), table_name="execution")
    op.drop_column("execution", "dispatched_at")
