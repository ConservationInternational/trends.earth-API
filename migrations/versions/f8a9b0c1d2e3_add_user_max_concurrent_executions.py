"""Add per-user max_concurrent_executions column

Allows configuring the execution queue concurrency limit on a per-user basis.
NULL means use the global default (MAX_CONCURRENT_EXECUTIONS_PER_USER).
"""

from alembic import op
import sqlalchemy as sa

revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column("max_concurrent_executions", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("user", "max_concurrent_executions")
