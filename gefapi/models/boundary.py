"""BOUNDARY MODELS"""

import datetime

from geoalchemy2 import Geometry

from gefapi import db


class AdminBoundary0(db.Model):
    """Administrative Boundary Level 0 (Countries)

    This model stores administrative boundary data from GeoBoundaries API.
    The schema is designed to be compatible with the GeoBoundaries API response
    documented at: https://www.geoboundaries.org/api.html

    API Endpoint: https://www.geoboundaries.org/api/current/gbOpen/[ISO]/ADM0/
    """

    __tablename__ = "admin_boundary_0"

    # Use the 'boundaryISO' field from geoBoundaries as primary key
    # (ISO 3-letter codes like 'AFG', 'ALB')
    id = db.Column(db.String(10), primary_key=True)

    # GeoBoundaries API metadata fields
    boundary_id = db.Column(db.String(100))  # boundaryID from API
    boundary_name = db.Column(db.String(255))  # boundaryName from API
    boundary_iso = db.Column(db.String(10))  # boundaryISO from API
    boundary_type = db.Column(db.String(10))  # boundaryType (e.g., "ADM0")
    boundary_canonical = db.Column(db.String(255))  # Canonical name if available
    boundary_source = db.Column(db.Text)  # Comma-separated list of sources
    boundary_license = db.Column(db.Text)  # License information
    license_detail = db.Column(db.Text)  # Additional license notes
    license_source = db.Column(db.Text)  # URL of license source
    source_data_update_date = db.Column(db.String(100))  # When data was integrated
    build_date = db.Column(db.String(100))  # When data was built

    # Geographic and political metadata
    continent = db.Column(db.String(100))
    unsdg_region = db.Column(db.String(100))  # UN SDG region
    unsdg_subregion = db.Column(db.String(100))  # UN SDG subregion
    world_bank_income_group = db.Column(db.String(100))

    # Geometry statistics from API
    adm_unit_count = db.Column(db.Integer)  # Number of admin units
    mean_vertices = db.Column(db.Float)
    min_vertices = db.Column(db.Integer)
    max_vertices = db.Column(db.Integer)
    mean_perimeter_km = db.Column(db.Float)
    min_perimeter_km = db.Column(db.Float)
    max_perimeter_km = db.Column(db.Float)
    mean_area_sqkm = db.Column(db.Float)
    min_area_sqkm = db.Column(db.Float)
    max_area_sqkm = db.Column(db.Float)

    # Download URLs from API (for reference/audit trail)
    static_download_link = db.Column(db.Text)  # Aggregate zip file
    geojson_download_url = db.Column(db.Text)  # GeoJSON download
    topojson_download_url = db.Column(db.Text)  # TopoJSON download
    simplified_geojson_url = db.Column(db.Text)  # Simplified geometry
    image_preview_url = db.Column(db.Text)  # PNG preview

    # PostGIS geometry column for proper spatial operations
    geometry = db.Column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True))

    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)

    def __repr__(self):
        return f"<AdminBoundary0(id='{self.id}', name='{self.boundary_name}')>"

    def to_dict(self, include_geometry=False, include_metadata=False):
        """Convert to dictionary for API response

        Args:
            include_geometry: If True, include geometry data (requires special handling)
            include_metadata: If True, include all GeoBoundaries metadata fields
        """
        result = {
            "id": self.id,
            "boundaryId": self.boundary_id,
            "boundaryName": self.boundary_name,
            "boundaryISO": self.boundary_iso,
            "boundaryType": self.boundary_type,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_metadata:
            result.update(
                {
                    "boundaryCanonical": self.boundary_canonical,
                    "boundarySource": self.boundary_source,
                    "boundaryLicense": self.boundary_license,
                    "licenseDetail": self.license_detail,
                    "licenseSource": self.license_source,
                    "sourceDataUpdateDate": self.source_data_update_date,
                    "buildDate": self.build_date,
                    "continent": self.continent,
                    "unsdgRegion": self.unsdg_region,
                    "unsdgSubregion": self.unsdg_subregion,
                    "worldBankIncomeGroup": self.world_bank_income_group,
                    "admUnitCount": self.adm_unit_count,
                    "meanVertices": self.mean_vertices,
                    "minVertices": self.min_vertices,
                    "maxVertices": self.max_vertices,
                    "meanPerimeterKm": self.mean_perimeter_km,
                    "minPerimeterKm": self.min_perimeter_km,
                    "maxPerimeterKm": self.max_perimeter_km,
                    "meanAreaSqkm": self.mean_area_sqkm,
                    "minAreaSqkm": self.min_area_sqkm,
                    "maxAreaSqkm": self.max_area_sqkm,
                    "staticDownloadLink": self.static_download_link,
                    "geojsonDownloadUrl": self.geojson_download_url,
                    "topojsonDownloadUrl": self.topojson_download_url,
                    "simplifiedGeojsonUrl": self.simplified_geojson_url,
                    "imagePreviewUrl": self.image_preview_url,
                }
            )

        # Note: Geometry serialization would require special handling if needed
        return result


class AdminBoundary1(db.Model):
    """Administrative Boundary Level 1 (States/Provinces)

    This model stores administrative boundary data from GeoBoundaries API.
    The schema is designed to be compatible with the GeoBoundaries API response
    documented at: https://www.geoboundaries.org/api.html

    API Endpoint: https://www.geoboundaries.org/api/current/gbOpen/[ISO]/ADM1/
    """

    __tablename__ = "admin_boundary_1"

    # Use the 'shapeID' field from geoBoundaries as primary key
    # (unique identifiers like 'AFG-ADM1-3_0_0_B1')
    shape_id = db.Column(db.String(100), primary_key=True)
    id = db.Column(db.String(10))  # Country ISO code (e.g., 'AFG')

    # GeoBoundaries API metadata fields
    boundary_id = db.Column(db.String(100))  # boundaryID from API
    boundary_name = db.Column(db.String(255))  # boundaryName from API
    boundary_iso = db.Column(db.String(10))  # boundaryISO from API
    boundary_type = db.Column(db.String(10))  # boundaryType (e.g., "ADM1")
    boundary_canonical = db.Column(db.String(255))  # Canonical name if available
    boundary_source = db.Column(db.Text)  # Comma-separated list of sources
    boundary_license = db.Column(db.Text)  # License information
    license_detail = db.Column(db.Text)  # Additional license notes
    license_source = db.Column(db.Text)  # URL of license source
    source_data_update_date = db.Column(db.String(100))  # When data was integrated
    build_date = db.Column(db.String(100))  # When data was built

    # Geographic and political metadata
    continent = db.Column(db.String(100))
    unsdg_region = db.Column(db.String(100))  # UN SDG region
    unsdg_subregion = db.Column(db.String(100))  # UN SDG subregion
    world_bank_income_group = db.Column(db.String(100))

    # Geometry statistics from API
    adm_unit_count = db.Column(db.Integer)  # Number of admin units
    mean_vertices = db.Column(db.Float)
    min_vertices = db.Column(db.Integer)
    max_vertices = db.Column(db.Integer)
    mean_perimeter_km = db.Column(db.Float)
    min_perimeter_km = db.Column(db.Float)
    max_perimeter_km = db.Column(db.Float)
    mean_area_sqkm = db.Column(db.Float)
    min_area_sqkm = db.Column(db.Float)
    max_area_sqkm = db.Column(db.Float)

    # Download URLs from API (for reference/audit trail)
    static_download_link = db.Column(db.Text)  # Aggregate zip file
    geojson_download_url = db.Column(db.Text)  # GeoJSON download
    topojson_download_url = db.Column(db.Text)  # TopoJSON download
    simplified_geojson_url = db.Column(db.Text)  # Simplified geometry
    image_preview_url = db.Column(db.Text)  # PNG preview

    # PostGIS geometry column for proper spatial operations
    geometry = db.Column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True))

    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)

    # Foreign key relationship to AdminBoundary0
    country = db.relationship(
        "AdminBoundary0",
        foreign_keys=[id],
        primaryjoin="AdminBoundary1.id == AdminBoundary0.id",
        backref="admin1_boundaries",
        uselist=False,
    )

    def __repr__(self):
        return (
            f"<AdminBoundary1(shape_id='{self.shape_id}', "
            f"name='{self.boundary_name}', country='{self.id}')>"
        )

    def to_dict(self, include_geometry=False, include_metadata=False):
        """Convert to dictionary for API response

        Args:
            include_geometry: If True, include geometry data (requires special handling)
            include_metadata: If True, include all GeoBoundaries metadata fields
        """
        result = {
            "shapeId": self.shape_id,
            "id": self.id,
            "boundaryId": self.boundary_id,
            "boundaryName": self.boundary_name,
            "boundaryISO": self.boundary_iso,
            "boundaryType": self.boundary_type,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_metadata:
            result.update(
                {
                    "boundaryCanonical": self.boundary_canonical,
                    "boundarySource": self.boundary_source,
                    "boundaryLicense": self.boundary_license,
                    "licenseDetail": self.license_detail,
                    "licenseSource": self.license_source,
                    "sourceDataUpdateDate": self.source_data_update_date,
                    "buildDate": self.build_date,
                    "continent": self.continent,
                    "unsdgRegion": self.unsdg_region,
                    "unsdgSubregion": self.unsdg_subregion,
                    "worldBankIncomeGroup": self.world_bank_income_group,
                    "admUnitCount": self.adm_unit_count,
                    "meanVertices": self.mean_vertices,
                    "minVertices": self.min_vertices,
                    "maxVertices": self.max_vertices,
                    "meanPerimeterKm": self.mean_perimeter_km,
                    "minPerimeterKm": self.min_perimeter_km,
                    "maxPerimeterKm": self.max_perimeter_km,
                    "meanAreaSqkm": self.mean_area_sqkm,
                    "minAreaSqkm": self.min_area_sqkm,
                    "maxAreaSqkm": self.max_area_sqkm,
                    "staticDownloadLink": self.static_download_link,
                    "geojsonDownloadUrl": self.geojson_download_url,
                    "topojsonDownloadUrl": self.topojson_download_url,
                    "simplifiedGeojsonUrl": self.simplified_geojson_url,
                    "imagePreviewUrl": self.image_preview_url,
                }
            )

        # Note: Geometry serialization would require special handling if needed
        return result
