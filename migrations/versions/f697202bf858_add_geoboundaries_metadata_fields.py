"""Add GeoBoundaries API metadata fields to boundary tables

Revision ID: f697202bf858
Revises: 17c881a78966
Create Date: 2025-01-21 14:30:00.000000

This migration adds comprehensive metadata fields from the GeoBoundaries API
to both AdminBoundary0 and AdminBoundary1 tables. These fields store information
about data sources, licenses, build dates, statistics, and download URLs.

API Documentation: https://www.geoboundaries.org/api.html
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f697202bf858"
down_revision = "17c881a78966"
branch_labels = None
depends_on = None


def upgrade():
    """Add GeoBoundaries API metadata fields to boundary tables."""

    # Add metadata fields to admin_boundary_0 (Countries)
    with op.batch_alter_table("admin_boundary_0", schema=None) as batch_op:
        # GeoBoundaries API metadata fields
        batch_op.add_column(sa.Column("boundary_id", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("boundary_name", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("boundary_iso", sa.String(10), nullable=True))
        batch_op.add_column(sa.Column("boundary_type", sa.String(10), nullable=True))
        batch_op.add_column(
            sa.Column("boundary_canonical", sa.String(255), nullable=True)
        )
        batch_op.add_column(sa.Column("boundary_source", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("boundary_license", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("license_detail", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("license_source", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("source_data_update_date", sa.String(100), nullable=True)
        )
        batch_op.add_column(sa.Column("build_date", sa.String(100), nullable=True))

        # Geographic and political metadata
        batch_op.add_column(sa.Column("continent", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("unsdg_region", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("unsdg_subregion", sa.String(100), nullable=True))
        batch_op.add_column(
            sa.Column("world_bank_income_group", sa.String(100), nullable=True)
        )

        # Geometry statistics from API
        batch_op.add_column(sa.Column("adm_unit_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("mean_vertices", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("min_vertices", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("max_vertices", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("mean_perimeter_km", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("min_perimeter_km", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("max_perimeter_km", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("mean_area_sqkm", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("min_area_sqkm", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("max_area_sqkm", sa.Float(), nullable=True))

        # Download URLs from API (for reference/audit trail)
        batch_op.add_column(sa.Column("static_download_link", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("geojson_download_url", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("topojson_download_url", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("simplified_geojson_url", sa.Text(), nullable=True)
        )
        batch_op.add_column(sa.Column("image_preview_url", sa.Text(), nullable=True))

    # Add the same metadata fields to admin_boundary_1 (States/Provinces)
    with op.batch_alter_table("admin_boundary_1", schema=None) as batch_op:
        # GeoBoundaries API metadata fields
        batch_op.add_column(sa.Column("boundary_id", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("boundary_name", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("boundary_iso", sa.String(10), nullable=True))
        batch_op.add_column(sa.Column("boundary_type", sa.String(10), nullable=True))
        batch_op.add_column(
            sa.Column("boundary_canonical", sa.String(255), nullable=True)
        )
        batch_op.add_column(sa.Column("boundary_source", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("boundary_license", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("license_detail", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("license_source", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("source_data_update_date", sa.String(100), nullable=True)
        )
        batch_op.add_column(sa.Column("build_date", sa.String(100), nullable=True))

        # Geographic and political metadata
        batch_op.add_column(sa.Column("continent", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("unsdg_region", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("unsdg_subregion", sa.String(100), nullable=True))
        batch_op.add_column(
            sa.Column("world_bank_income_group", sa.String(100), nullable=True)
        )

        # Geometry statistics from API
        batch_op.add_column(sa.Column("adm_unit_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("mean_vertices", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("min_vertices", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("max_vertices", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("mean_perimeter_km", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("min_perimeter_km", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("max_perimeter_km", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("mean_area_sqkm", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("min_area_sqkm", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("max_area_sqkm", sa.Float(), nullable=True))

        # Download URLs from API (for reference/audit trail)
        batch_op.add_column(sa.Column("static_download_link", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("geojson_download_url", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("topojson_download_url", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("simplified_geojson_url", sa.Text(), nullable=True)
        )
        batch_op.add_column(sa.Column("image_preview_url", sa.Text(), nullable=True))


def downgrade():
    """Remove GeoBoundaries API metadata fields from boundary tables."""

    # Remove metadata fields from admin_boundary_1
    with op.batch_alter_table("admin_boundary_1", schema=None) as batch_op:
        batch_op.drop_column("image_preview_url")
        batch_op.drop_column("simplified_geojson_url")
        batch_op.drop_column("topojson_download_url")
        batch_op.drop_column("geojson_download_url")
        batch_op.drop_column("static_download_link")
        batch_op.drop_column("max_area_sqkm")
        batch_op.drop_column("min_area_sqkm")
        batch_op.drop_column("mean_area_sqkm")
        batch_op.drop_column("max_perimeter_km")
        batch_op.drop_column("min_perimeter_km")
        batch_op.drop_column("mean_perimeter_km")
        batch_op.drop_column("max_vertices")
        batch_op.drop_column("min_vertices")
        batch_op.drop_column("mean_vertices")
        batch_op.drop_column("adm_unit_count")
        batch_op.drop_column("world_bank_income_group")
        batch_op.drop_column("unsdg_subregion")
        batch_op.drop_column("unsdg_region")
        batch_op.drop_column("continent")
        batch_op.drop_column("build_date")
        batch_op.drop_column("source_data_update_date")
        batch_op.drop_column("license_source")
        batch_op.drop_column("license_detail")
        batch_op.drop_column("boundary_license")
        batch_op.drop_column("boundary_source")
        batch_op.drop_column("boundary_canonical")
        batch_op.drop_column("boundary_type")
        batch_op.drop_column("boundary_iso")
        batch_op.drop_column("boundary_name")
        batch_op.drop_column("boundary_id")

    # Remove metadata fields from admin_boundary_0
    with op.batch_alter_table("admin_boundary_0", schema=None) as batch_op:
        batch_op.drop_column("image_preview_url")
        batch_op.drop_column("simplified_geojson_url")
        batch_op.drop_column("topojson_download_url")
        batch_op.drop_column("geojson_download_url")
        batch_op.drop_column("static_download_link")
        batch_op.drop_column("max_area_sqkm")
        batch_op.drop_column("min_area_sqkm")
        batch_op.drop_column("mean_area_sqkm")
        batch_op.drop_column("max_perimeter_km")
        batch_op.drop_column("min_perimeter_km")
        batch_op.drop_column("mean_perimeter_km")
        batch_op.drop_column("max_vertices")
        batch_op.drop_column("min_vertices")
        batch_op.drop_column("mean_vertices")
        batch_op.drop_column("adm_unit_count")
        batch_op.drop_column("world_bank_income_group")
        batch_op.drop_column("unsdg_subregion")
        batch_op.drop_column("unsdg_region")
        batch_op.drop_column("continent")
        batch_op.drop_column("build_date")
        batch_op.drop_column("source_data_update_date")
        batch_op.drop_column("license_source")
        batch_op.drop_column("license_detail")
        batch_op.drop_column("boundary_license")
        batch_op.drop_column("boundary_source")
        batch_op.drop_column("boundary_canonical")
        batch_op.drop_column("boundary_type")
        batch_op.drop_column("boundary_iso")
        batch_op.drop_column("boundary_name")
        batch_op.drop_column("boundary_id")
