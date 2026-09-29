"""Add a per-client logo for the hosted sign-in and register pages."""

from alembic import op
import sqlalchemy as sa

revision = "a8d3f1c62b97"
down_revision = "c4e91b7a2d6f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "oauth_clients", sa.Column("logo_url", sa.String(length=500), nullable=True)
    )


def downgrade():
    op.drop_column("oauth_clients", "logo_url")
