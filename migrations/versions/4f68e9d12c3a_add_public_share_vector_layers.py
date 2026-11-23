"""Support PMTiles vector layers in public shares.

Revision ID: 4f68e9d12c3a
Revises: 8c4f2a9d1e73
"""

from alembic import op
import sqlalchemy as sa

revision = "4f68e9d12c3a"
down_revision = "8c4f2a9d1e73"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "public_share_layer",
        sa.Column("kind", sa.String(16), nullable=False, server_default="raster"),
    )
    op.add_column(
        "public_share_layer", sa.Column("vector_metadata", sa.JSON(), nullable=True)
    )
    op.alter_column("public_share_layer", "band", nullable=True)


def downgrade():
    # Old API versions cannot serve vector records or clean their metadata.
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT 1 FROM public_share_layer WHERE kind = 'vector' LIMIT 1")
    ).first():
        raise RuntimeError("Remove vector shares before downgrading this migration")
    op.alter_column("public_share_layer", "band", nullable=False)
    op.drop_column("public_share_layer", "vector_metadata")
    op.drop_column("public_share_layer", "kind")
