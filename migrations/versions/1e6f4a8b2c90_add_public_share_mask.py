"""Add an optional display mask to public web-map shares."""

from alembic import op
import sqlalchemy as sa

revision = "1e6f4a8b2c90"
down_revision = "ce4a8f2d109b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("public_share", sa.Column("mask", sa.JSON(), nullable=True))
    op.add_column(
        "public_share",
        sa.Column("mask_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("public_share", "mask_version")
    op.drop_column("public_share", "mask")
