"""Track removed layers until their cached tiles and private objects are cleaned."""

from alembic import op
import sqlalchemy as sa

revision = "9c4f7a3d8b20"
down_revision = "1e6f4a8b2c90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("public_share_layer", sa.Column("removed_at", sa.DateTime()))
    op.create_index(
        "ix_public_share_layer_removed_at", "public_share_layer", ["removed_at"]
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM public_share_layer WHERE removed_at IS NOT NULL LIMIT 1"
            )
        )
        .first()
    ):
        raise RuntimeError("Wait for removed layer cleanup before downgrading")
    op.drop_index("ix_public_share_layer_removed_at", table_name="public_share_layer")
    op.drop_column("public_share_layer", "removed_at")
