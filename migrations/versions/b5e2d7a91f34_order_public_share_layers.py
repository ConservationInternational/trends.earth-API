"""Let owners reorder the layers of a shared web map."""

from alembic import op
import sqlalchemy as sa

revision = "b5e2d7a91f34"
down_revision = "9c4f7a3d8b20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "public_share_layer",
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        UPDATE public_share_layer AS layer
        SET position = ordered.position
        FROM (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY share_id ORDER BY created_at, id
            ) - 1 AS position
            FROM public_share_layer
        ) AS ordered
        WHERE layer.id = ordered.id
        """
    )


def downgrade():
    op.drop_column("public_share_layer", "position")
