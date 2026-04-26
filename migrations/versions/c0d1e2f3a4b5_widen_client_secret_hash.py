"""Widen service_client.client_secret_hash from 64 to 256 chars

Revision ID: c0d1e2f3a4b5
Revises: b8c9d0e1f2a3
Create Date: 2026-04-25

Widens the client_secret_hash column to accommodate scrypt hashes
(werkzeug format, ~95 chars) in addition to the legacy SHA-256 hex
digests (64 chars).  Existing rows are unaffected; their legacy SHA-256
hashes continue to work via the backward-compatible verify_secret() path.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c0d1e2f3a4b5"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    """Widen client_secret_hash to 256 chars for scrypt hashes."""
    op.alter_column(
        "service_client",
        "client_secret_hash",
        existing_type=sa.String(64),
        type_=sa.String(256),
        existing_nullable=False,
    )


def downgrade():
    """Narrow client_secret_hash back to 64 chars (SHA-256 only)."""
    op.alter_column(
        "service_client",
        "client_secret_hash",
        existing_type=sa.String(256),
        type_=sa.String(64),
        existing_nullable=False,
    )
