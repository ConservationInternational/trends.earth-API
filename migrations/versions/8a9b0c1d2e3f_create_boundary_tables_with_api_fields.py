"""Create boundary tables with geoBoundaries API field names

Revision ID: 8a9b0c1d2e3f
Revises: 2c4f8e1a9b3d
Create Date: 2025-01-26 10:00:00.000000

This migration creates three administrative boundary tables:
1. admin_boundary_0_metadata - ADM0 boundary API metadata
2. admin_boundary_1_metadata - ADM1 boundary API metadata  
3. admin_boundary_1_unit - Individual ADM1 units from GeoJSON

Database column names exactly match geoBoundaries API response field names.

Key Features:
- Metadata tables store complete API responses
- Unit table stores individual ADM1 units (shapeID, shapeName) extracted from GeoJSON
- Support for all three geoBoundaries release types (gbOpen, gbHumanitarian, gbAuthoritative)
- Foreign key relationships between units and metadata tables

API Documentation: https://www.geoboundaries.org/api.html
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "8a9b0c1d2e3f"
down_revision = "2c4f8e1a9b3d"
branch_labels = None
depends_on = None


def upgrade():
    """Create administrative boundary tables with three-table structure."""

    # Create admin_boundary_0_metadata table (country-level API metadata)
    op.create_table(
        "admin_boundary_0_metadata",
        # Composite primary key: boundaryISO + releaseType
        sa.Column("boundaryISO", sa.String(10), primary_key=True),
        sa.Column("releaseType", sa.String(20), primary_key=True),
        # Core identification fields matching geoBoundaries API
        sa.Column("boundaryID", sa.String(100), nullable=True),
        sa.Column("boundaryName", sa.String(255), nullable=True),
        sa.Column("boundaryType", sa.String(10), nullable=True),
        sa.Column("boundaryCanonical", sa.String(255), nullable=True),
        sa.Column("boundaryYearRepresented", sa.String(50), nullable=True),
        # Data source and licensing fields
        sa.Column("boundarySource", sa.Text(), nullable=True),
        sa.Column("boundaryLicense", sa.Text(), nullable=True),
        sa.Column("licenseDetail", sa.Text(), nullable=True),
        sa.Column("licenseSource", sa.Text(), nullable=True),
        sa.Column("sourceDataUpdateDate", sa.String(100), nullable=True),
        sa.Column("buildDate", sa.String(100), nullable=True),
        # Geographic and political metadata
        sa.Column("Continent", sa.String(100), nullable=True),
        sa.Column("UNSDG_region", sa.String(100), nullable=True),
        sa.Column("UNSDG_subregion", sa.String(100), nullable=True),
        sa.Column("worldBankIncomeGroup", sa.String(100), nullable=True),
        # Geometry statistics from API
        sa.Column("admUnitCount", sa.Integer(), nullable=True),
        sa.Column("meanVertices", sa.Float(), nullable=True),
        sa.Column("minVertices", sa.Integer(), nullable=True),
        sa.Column("maxVertices", sa.Integer(), nullable=True),
        sa.Column("meanPerimeterLengthKM", sa.Float(), nullable=True),
        sa.Column("minPerimeterLengthKM", sa.Float(), nullable=True),
        sa.Column("maxPerimeterLengthKM", sa.Float(), nullable=True),
        sa.Column("meanAreaSqKM", sa.Float(), nullable=True),
        sa.Column("minAreaSqKM", sa.Float(), nullable=True),
        sa.Column("maxAreaSqKM", sa.Float(), nullable=True),
        # Download URLs from API
        sa.Column("staticDownloadLink", sa.Text(), nullable=True),
        sa.Column("gjDownloadURL", sa.Text(), nullable=True),
        sa.Column("tjDownloadURL", sa.Text(), nullable=True),
        sa.Column("simplifiedGeometryGeoJSON", sa.Text(), nullable=True),
        sa.Column("imagePreview", sa.Text(), nullable=True),
        # Timestamps
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

    # Create admin_boundary_1_metadata table (ADM1 API metadata)
    op.create_table(
        "admin_boundary_1_metadata",
        # Composite primary key: boundaryISO + releaseType
        sa.Column("boundaryISO", sa.String(10), primary_key=True),
        sa.Column("releaseType", sa.String(20), primary_key=True),
        # Core identification fields matching geoBoundaries API
        sa.Column("boundaryID", sa.String(100), nullable=True),
        sa.Column("boundaryName", sa.String(255), nullable=True),
        sa.Column("boundaryType", sa.String(10), nullable=True),
        sa.Column("boundaryCanonical", sa.String(255), nullable=True),
        sa.Column("boundaryYearRepresented", sa.String(50), nullable=True),
        # Data source and licensing fields
        sa.Column("boundarySource", sa.Text(), nullable=True),
        sa.Column("boundaryLicense", sa.Text(), nullable=True),
        sa.Column("licenseDetail", sa.Text(), nullable=True),
        sa.Column("licenseSource", sa.Text(), nullable=True),
        sa.Column("sourceDataUpdateDate", sa.String(100), nullable=True),
        sa.Column("buildDate", sa.String(100), nullable=True),
        # Geographic and political metadata
        sa.Column("Continent", sa.String(100), nullable=True),
        sa.Column("UNSDG_region", sa.String(100), nullable=True),
        sa.Column("UNSDG_subregion", sa.String(100), nullable=True),
        sa.Column("worldBankIncomeGroup", sa.String(100), nullable=True),
        # Geometry statistics from API (for all ADM1 units in country)
        sa.Column("admUnitCount", sa.Integer(), nullable=True),
        sa.Column("meanVertices", sa.Float(), nullable=True),
        sa.Column("minVertices", sa.Integer(), nullable=True),
        sa.Column("maxVertices", sa.Integer(), nullable=True),
        sa.Column("meanPerimeterLengthKM", sa.Float(), nullable=True),
        sa.Column("minPerimeterLengthKM", sa.Float(), nullable=True),
        sa.Column("maxPerimeterLengthKM", sa.Float(), nullable=True),
        sa.Column("meanAreaSqKM", sa.Float(), nullable=True),
        sa.Column("minAreaSqKM", sa.Float(), nullable=True),
        sa.Column("maxAreaSqKM", sa.Float(), nullable=True),
        # Download URLs from API
        sa.Column("staticDownloadLink", sa.Text(), nullable=True),
        sa.Column("gjDownloadURL", sa.Text(), nullable=True),
        sa.Column("tjDownloadURL", sa.Text(), nullable=True),
        sa.Column("simplifiedGeometryGeoJSON", sa.Text(), nullable=True),
        sa.Column("imagePreview", sa.Text(), nullable=True),
        # Timestamps
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

    # Create admin_boundary_1_unit table (individual ADM1 units)
    op.create_table(
        "admin_boundary_1_unit",
        # Composite primary key: shapeID + releaseType
        sa.Column("shapeID", sa.String(100), primary_key=True),
        sa.Column("releaseType", sa.String(20), primary_key=True),
        # Foreign key to parent country
        sa.Column("boundaryISO", sa.String(10), nullable=False),
        # Unit identification from GeoJSON properties
        sa.Column("shapeName", sa.String(255), nullable=True),
        # Timestamps
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

    # Create foreign key constraints
    op.create_foreign_key(
        "fk_adm1_unit_to_adm0_metadata",
        "admin_boundary_1_unit",
        "admin_boundary_0_metadata",
        ["boundaryISO", "releaseType"],
        ["boundaryISO", "releaseType"],
    )
    
    op.create_foreign_key(
        "fk_adm1_unit_to_adm1_metadata",
        "admin_boundary_1_unit",
        "admin_boundary_1_metadata",
        ["boundaryISO", "releaseType"],
        ["boundaryISO", "releaseType"],
    )

    # Create indexes for efficient querying
    op.create_index(
        "idx_adm0_metadata_release_type",
        "admin_boundary_0_metadata",
        ["releaseType"],
    )
    op.create_index(
        "idx_adm1_metadata_release_type",
        "admin_boundary_1_metadata",
        ["releaseType"],
    )
    op.create_index(
        "idx_adm1_unit_country",
        "admin_boundary_1_unit",
        ["boundaryISO", "releaseType"],
    )
    op.create_index(
        "idx_adm1_unit_release_type",
        "admin_boundary_1_unit",
        ["releaseType"],
    )


def downgrade():
    """Remove administrative boundary tables."""
    op.drop_index("idx_adm1_unit_release_type", table_name="admin_boundary_1_unit")
    op.drop_index("idx_adm1_unit_country", table_name="admin_boundary_1_unit")
    op.drop_index("idx_adm1_metadata_release_type", table_name="admin_boundary_1_metadata")
    op.drop_index("idx_adm0_metadata_release_type", table_name="admin_boundary_0_metadata")
    
    op.drop_constraint(
        "fk_adm1_unit_to_adm1_metadata", "admin_boundary_1_unit", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_adm1_unit_to_adm0_metadata", "admin_boundary_1_unit", type_="foreignkey"
    )
    
    op.drop_table("admin_boundary_1_unit")
    op.drop_table("admin_boundary_1_metadata")
    op.drop_table("admin_boundary_0_metadata")