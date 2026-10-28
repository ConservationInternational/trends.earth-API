"""Add publisher-controlled Batch execution policy."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "6f3a90c2d8e1"
down_revision = "b5d7e9f1a3c4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "script",
        sa.Column("batch_execution_config", postgresql.JSONB(), nullable=True),
    )


def downgrade():
    op.drop_column("script", "batch_execution_config")
