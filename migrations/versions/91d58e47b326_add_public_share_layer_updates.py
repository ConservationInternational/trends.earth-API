"""Support additive updates to published public shares.

Revision ID: 91d58e47b326
Revises: 4f68e9d12c3a
"""

from alembic import op
import sqlalchemy as sa

revision = "91d58e47b326"
down_revision = "4f68e9d12c3a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "public_share",
        sa.Column("update_state", sa.String(20), nullable=False, server_default="idle"),
    )
    op.add_column(
        "public_share_layer",
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE public_share_layer SET published = TRUE "
        "WHERE checksum_sha256 IS NOT NULL"
    )


def downgrade():
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT 1 FROM public_share WHERE update_state != 'idle' LIMIT 1"
        )
    ).first():
        raise RuntimeError("Complete public-share updates before downgrading")
    op.drop_column("public_share_layer", "published")
    op.drop_column("public_share", "update_state")
