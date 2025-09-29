"""Add administrative boundary tables

Revision ID: 17c881a78966
Revises: 79d3f10e7527
Create Date: 2025-01-25 12:00:00.000000

"""

from alembic import op
from geoalchemy2 import Geometry
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "17c881a78966"
down_revision = "79d3f10e7527"
branch_labels = None
depends_on = None


def upgrade():
    """Add administrative boundary tables for geoBoundaries data."""
    # Create admin_boundary_0 table (countries)
    op.create_table(
        "admin_boundary_0",
        sa.Column("id", sa.String(10), primary_key=True),
        sa.Column("shape_group", sa.String(100), nullable=True),
        sa.Column("shape_type", sa.String(50), nullable=True),
        sa.Column("shape_name", sa.String(255), nullable=True),
        sa.Column(
            "geometry",
            Geometry("MULTIPOLYGON", srid=4326, spatial_index=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Create admin_boundary_1 table (states/provinces)
    op.create_table(
        "admin_boundary_1",
        sa.Column("shape_id", sa.String(100), primary_key=True),
        sa.Column("id", sa.String(10), nullable=True),
        sa.Column("shape_name", sa.String(255), nullable=True),
        sa.Column("shape_group", sa.String(100), nullable=True),
        sa.Column("shape_type", sa.String(50), nullable=True),
        sa.Column(
            "geometry",
            Geometry("MULTIPOLYGON", srid=4326, spatial_index=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Create foreign key constraint
    op.create_foreign_key(
        "fk_admin_boundary_1_country_id",
        "admin_boundary_1",
        "admin_boundary_0",
        ["id"],
        ["id"],
    )

    # Create indexes for common queries
    op.create_index(
        "idx_admin_boundary_0_shape_name", "admin_boundary_0", ["shape_name"]
    )
    op.create_index(
        "idx_admin_boundary_1_shape_name", "admin_boundary_1", ["shape_name"]
    )
    op.create_index("idx_admin_boundary_1_country_id", "admin_boundary_1", ["id"])


def downgrade():
    """Remove administrative boundary tables."""
    op.drop_table("admin_boundary_1")
    op.drop_table("admin_boundary_0")
