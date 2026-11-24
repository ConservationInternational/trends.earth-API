"""Store encrypted public-share links so owners can view them again.

Revision ID: b6e1c4d9a2f7
Revises: 91d58e47b326
"""

from alembic import op
import sqlalchemy as sa

revision = "b6e1c4d9a2f7"
down_revision = "91d58e47b326"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "public_share", sa.Column("link_ciphertext", sa.Text(), nullable=True)
    )


def downgrade():
    op.drop_column("public_share", "link_ciphertext")
