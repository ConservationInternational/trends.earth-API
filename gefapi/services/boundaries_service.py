"""
Boundaries Service for administrative boundary operations.
Handles complex queries and business logic for geoBoundaries data.
"""

import logging

from geoalchemy2 import WKTElement
from geoalchemy2.functions import ST_AsGeoJSON, ST_Contains
from sqlalchemy.orm import Query

from gefapi import db
from gefapi.models.boundary import AdminBoundary0, AdminBoundary1

logger = logging.getLogger(__name__)


class BoundariesService:
    """Service class for administrative boundary operations."""

    @staticmethod
    def get_boundaries(
        levels: list[int],
        filters: dict | None = None,
        format_type: str = "full",
        page: int = 1,
        per_page: int = 100,
    ) -> tuple[list[dict], int]:
        """
        Get boundaries from multiple administrative levels with filters.

        Args:
            levels: List of administrative levels (e.g., [0, 1])
            filters: Dictionary of filter criteria
            format_type: Response format ('full' or 'table')
            page: Page number for pagination (default: 1)
            per_page: Results per page (default: 100)

        Returns:
            Tuple of (results list, total count)
        """
        all_results = []
        total_count = 0

        # Query each level separately and combine results
        for level in levels:
            model = AdminBoundary0 if level == 0 else AdminBoundary1
            query = db.session.query(model)

            # Apply filters if provided
            if filters:
                query = BoundariesService._apply_filters(query, model, filters)

            # Get results for this level
            level_results = query.all()

            # Format results and add level information
            for boundary in level_results:
                result = BoundariesService._format_boundary(boundary, format_type)
                result["level"] = level
                result["created_at"] = (
                    boundary.created_at.isoformat() if boundary.created_at else None
                )
                result["updated_at"] = (
                    boundary.updated_at.isoformat() if boundary.updated_at else None
                )
                all_results.append(result)

            total_count += len(level_results)

        # Apply pagination to combined results
        if page < 1:
            page = 1
        if per_page < 1:
            per_page = 100

        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_results = all_results[start_idx:end_idx]

        return paginated_results, total_count

    @staticmethod
    def _apply_filters(query: Query, model, filters: dict) -> Query:
        """Apply filters to query based on model type."""
        for field, value in filters.items():
            if field.endswith("_since") and hasattr(model, field.replace("_since", "")):
                # Handle timestamp filters (e.g., created_at_since, updated_at_since)
                column = getattr(model, field.replace("_since", ""))
                query = query.filter(column >= value)
            elif hasattr(model, field):
                column = getattr(model, field)
                if isinstance(value, list):
                    query = query.filter(column.in_(value))
                elif isinstance(value, str) and "%" in value:
                    query = query.filter(column.ilike(value))
                else:
                    query = query.filter(column == value)
        return query

    @staticmethod
    def _format_boundary(boundary, format_type: str) -> dict:
        """Format boundary for API response."""
        result = {
            "id": boundary.id,
            "shape_name": boundary.shape_name,
            "shape_type": boundary.shape_type,
            "shape_group": boundary.shape_group,
        }

        # Add shape_id for AdminBoundary1 objects
        if hasattr(boundary, "shape_id"):
            result["shape_id"] = boundary.shape_id

        # Include geometry when format is 'full'
        if format_type == "full" and boundary.geometry is not None:
            # Convert PostGIS geometry to GeoJSON using GeoAlchemy2
            geojson_result = db.session.query(ST_AsGeoJSON(boundary.geometry)).scalar()

            if geojson_result:
                import json

                result["geometry"] = json.loads(geojson_result)

        return result

    @staticmethod
    def validate_coordinates(lon: float, lat: float) -> bool:
        """Validate longitude and latitude coordinates."""
        return -180 <= lon <= 180 and -90 <= lat <= 90

    @staticmethod
    def validate_point_coordinates(lat: float, lon: float) -> bool:
        """Validate point coordinates (lat, lon order for API consistency)."""
        return -90 <= lat <= 90 and -180 <= lon <= 180

    @staticmethod
    def get_boundary_statistics() -> dict:
        """Get statistics about available boundary data."""
        try:
            adm0_count = db.session.query(AdminBoundary0).count()
            adm1_count = db.session.query(AdminBoundary1).count()

            levels_available = []
            if adm0_count > 0:
                levels_available.append(0)
            if adm1_count > 1:
                levels_available.append(1)

            return {
                "total_countries": adm0_count,
                "total_admin1_units": adm1_count,
                "levels_available": levels_available,
            }
        except Exception as e:
            logger.error(f"Error getting boundary statistics: {str(e)}")
            return {
                "total_countries": 0,
                "total_admin1_units": 0,
                "levels_available": [],
            }

    @staticmethod
    def find_boundaries_containing_point(
        lon: float, lat: float, level: int | None = None
    ) -> list[dict]:
        """
        Find boundaries that contain the given point using PostGIS spatial functions.

        Args:
            lon: Longitude coordinate
            lat: Latitude coordinate
            level: Administrative level (0 or 1), if None search both

        Returns:
            List of boundaries containing the point
        """
        if not BoundariesService.validate_coordinates(lon, lat):
            raise ValueError("Invalid coordinates")

        results = []

        # Create point geometry for spatial query
        point = WKTElement(f"POINT({lon} {lat})", srid=4326)

        # Search admin level 0 boundaries
        if level is None or level == 0:
            admin0_query = db.session.query(AdminBoundary0).filter(
                ST_Contains(AdminBoundary0.geometry, point)
            )

            for boundary in admin0_query.all():
                results.append(
                    {
                        "level": 0,
                        "id": boundary.id,
                        "shape_name": boundary.shape_name,
                        "shape_type": boundary.shape_type,
                        "shape_group": boundary.shape_group,
                    }
                )

        # Search admin level 1 boundaries
        if level is None or level == 1:
            admin1_query = db.session.query(AdminBoundary1).filter(
                ST_Contains(AdminBoundary1.geometry, point)
            )

            for boundary in admin1_query.all():
                results.append(
                    {
                        "level": 1,
                        "shape_id": boundary.shape_id,
                        "id": boundary.id,  # Country ID
                        "shape_name": boundary.shape_name,
                        "shape_type": boundary.shape_type,
                        "shape_group": boundary.shape_group,
                    }
                )

        return results

    @staticmethod
    def get_boundary_by_id(boundary_id: str) -> dict | None:
        """Get a specific boundary by ID from any administrative level."""
        # Try AdminBoundary0 first
        boundary = (
            db.session.query(AdminBoundary0)
            .filter(AdminBoundary0.id == boundary_id)
            .first()
        )

        if boundary:
            result = BoundariesService._format_boundary(boundary, "full")
            result["level"] = 0
            return result

        # Try AdminBoundary1 if not found in AdminBoundary0
        boundary = (
            db.session.query(AdminBoundary1)
            .filter(AdminBoundary1.id == boundary_id)
            .first()
        )

        if boundary:
            result = BoundariesService._format_boundary(boundary, "full")
            result["level"] = 1
            return result

        return None
