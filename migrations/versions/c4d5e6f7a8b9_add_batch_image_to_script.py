"""Add batch_image column to script table

Stores the full ECR image URI for batch compute_type scripts, allowing
the API to automatically resolve the container image without requiring
it in configuration.json.

Revision ID: c4d5e6f7a8b9
Revises: 8d2e4f6a1c3b
Create Date: 2026-06-28 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c4d5e6f7a8b9"
down_revision = "8d2e4f6a1c3b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "script",
        sa.Column("batch_image", sa.String(length=512), nullable=True),
    )


def downgrade():
    op.drop_column("script", "batch_image")
