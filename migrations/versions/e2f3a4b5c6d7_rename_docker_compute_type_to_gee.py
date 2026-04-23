"""Rename compute_type 'docker' to 'gee' for clarity.

The value 'docker' was misleading because it described the *orchestrator*
(Docker Swarm) rather than the *compute backend* (Google Earth Engine).
Both 'gee' and 'openeo' use Docker Swarm as their orchestrator, so the
distinction belongs in the separate ORCHESTRATOR setting.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-04-23 00:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    # Rename existing rows that still carry the old default value.
    op.execute("UPDATE script SET compute_type = 'gee' WHERE compute_type = 'docker'")

    # Update the column's server-side default so new rows are created correctly.
    op.alter_column(
        "script",
        "compute_type",
        server_default="gee",
        existing_type=op.f("sa.String(length=40)"),
        existing_nullable=False,
    )


def downgrade():
    op.alter_column(
        "script",
        "compute_type",
        server_default="docker",
        existing_type=op.f("sa.String(length=40)"),
        existing_nullable=False,
    )
    op.execute("UPDATE script SET compute_type = 'docker' WHERE compute_type = 'gee'")
