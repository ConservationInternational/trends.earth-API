"""Index public-share path-token lookups.

Revision ID: ce4a8f2d109b
Revises: b6e1c4d9a2f7
"""

from alembic import op

revision = "ce4a8f2d109b"
down_revision = "b6e1c4d9a2f7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_public_share_token_hash", "public_share", ["token_hash"])


def downgrade():
    op.drop_index("ix_public_share_token_hash", table_name="public_share")
