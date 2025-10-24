"""
Boundaries Service for administrative boundary operations.
Handles complex queries and business logic for geoBoundaries data.
Returns metadata and download URLs instead of geometries.
"""

import logging

from sqlalchemy.orm import Query

from gefapi import db
from gefapi.models.boundary import (
    AdminBoundary0Metadata,
    AdminBoundary1Metadata,
    AdminBoundary1Unit,
)

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

        Returns metadata and download URLs. The format_type parameter is kept for
        backwards compatibility but no longer affects the response (geometries are
        never included).

        Args:
            levels: List of administrative levels (e.g., [0, 1])
            filters: Dictionary of filter criteria
            format_type: Deprecated (kept for backwards compatibility)
            page: Page number for pagination (default: 1)
            per_page: Results per page (default: 100)

        Returns:
            Tuple of (results list, total count)
        """
        all_results = []
        total_count = 0

        # Query each level separately and combine results
        for level in levels:
            model = AdminBoundary0Metadata if level == 0 else AdminBoundary1Unit
            query = db.session.query(model)

            # Apply filters if provided
            if filters:
                query = BoundariesService._apply_filters(query, model, filters)

            # Get results for this level
            level_results = query.all()

            # Format results and add level information
            for boundary in level_results:
                result = BoundariesService._format_boundary(boundary)
                result["level"] = level
                result["createdAt"] = (
                    boundary.created_at.isoformat() if boundary.created_at else None
                )
                result["updatedAt"] = (
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
        """Apply filters to query based on model type.

        Note: name_filter is only supported for AdminBoundary0Metadata (ADM0).
        For AdminBoundary1Unit (ADM1), name filtering is not supported as there
        are no download links for individual admin1 units - only country-level
        downloads are available.
        """
        # Map API filter names to model field names
        field_mapping: dict[str, str] = {
            "iso_code": "boundaryISO",
            "name_filter": "boundaryName",  # Only for ADM0
            "release_type": "releaseType",
        }

        for field, value in filters.items():
            # Skip name_filter for AdminBoundary1Unit (admin1 level)
            # Names are only used in hierarchy endpoint, not for filtering
            if field == "name_filter" and hasattr(model, "shapeName"):
                logger.debug(
                    "Skipping name_filter for AdminBoundary1Unit - "
                    "name filtering only supported for ADM0"
                )
                continue

            # Map API field name to model field name (defaults to original field)
            model_field: str = field_mapping.get(field, field)  # type: ignore

            # Handle timestamp filters
            if field.endswith("_since"):
                timestamp_field = model_field.replace("_since", "")
                if hasattr(model, timestamp_field):
                    column = getattr(model, timestamp_field)
                    query = query.filter(column >= value)
            elif hasattr(model, model_field):
                column = getattr(model, model_field)
                if isinstance(value, list):
                    query = query.filter(column.in_(value))
                elif isinstance(value, str) and "%" in value:
                    query = query.filter(column.ilike(value))
                else:
                    query = query.filter(column == value)
        return query

    @staticmethod
    def _format_boundary(boundary) -> dict:
        """Format boundary for API response.

        Returns metadata using exact geoBoundaries field names.
        For ADM0Metadata: includes complete API response with download URLs
        For ADM1Metadata: includes complete API response with download URLs
        For ADM1Unit: includes individual unit metadata (shapeID, shapeName)
        """
        # Common fields for all boundary types
        result = {
            "boundaryISO": boundary.boundaryISO,
            "releaseType": boundary.releaseType,
        }

        # Add metadata fields if available
        # (AdminBoundary0Metadata or AdminBoundary1Metadata)
        if hasattr(boundary, "gjDownloadURL"):
            result.update(
                {
                    "boundaryName": boundary.boundaryName,
                    "boundaryType": boundary.boundaryType,
                    "boundaryID": boundary.boundaryID,
                    "Continent": boundary.Continent,
                    "buildDate": boundary.buildDate,
                    "gjDownloadURL": boundary.gjDownloadURL,
                    "tjDownloadURL": boundary.tjDownloadURL,
                    "staticDownloadLink": boundary.staticDownloadLink,
                    "simplifiedGeometryGeoJSON": boundary.simplifiedGeometryGeoJSON,
                    "imagePreview": boundary.imagePreview,
                }
            )

        # Add unit-specific fields (AdminBoundary1Unit)
        if hasattr(boundary, "shapeID"):
            result["shapeID"] = boundary.shapeID
        if hasattr(boundary, "shapeName"):
            result["shapeName"] = boundary.shapeName

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
            adm0_count = db.session.query(AdminBoundary0Metadata).count()
            adm1_count = db.session.query(AdminBoundary1Unit).count()

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
        Find boundaries that contain the given point.

        DEPRECATED: This method cannot work without stored geometries.
        Clients should use the gjDownloadURL to fetch the GeoJSON and perform
        spatial queries client-side or use a dedicated spatial service.

        Args:
            lon: Longitude coordinate
            lat: Latitude coordinate
            level: Administrative level (0 or 1), if None search both

        Returns:
            Empty list (spatial queries not supported without geometries)
        """
        logger.warning(
            "Spatial queries are not supported - geometries are not stored. "
            "Use gjDownloadURL to fetch GeoJSON for client-side spatial operations."
        )
        return []

    @staticmethod
    def get_boundary_by_id(
        boundary_id: str, release_type: str = "gbOpen"
    ) -> dict | None:
        """Get a specific boundary by ID from any administrative level.

        Args:
            boundary_id: The boundary ID (boundaryISO for ADM0, shapeID for ADM1)
            release_type: The release type (gbOpen, gbHumanitarian, gbAuthoritative)
        """
        # Try AdminBoundary0Metadata first (by boundaryISO)
        boundary = (
            db.session.query(AdminBoundary0Metadata)
            .filter(
                AdminBoundary0Metadata.boundaryISO == boundary_id,
                AdminBoundary0Metadata.releaseType == release_type,
            )
            .first()
        )

        if boundary:
            result = BoundariesService._format_boundary(boundary)
            result["level"] = 0
            return result

        # Try AdminBoundary1Unit if not found in AdminBoundary0Metadata (by shapeID)
        boundary = (
            db.session.query(AdminBoundary1Unit)
            .filter(
                AdminBoundary1Unit.shapeID == boundary_id,
                AdminBoundary1Unit.releaseType == release_type,
            )
            .first()
        )

        if boundary:
            result = BoundariesService._format_boundary(boundary)
            result["level"] = 1
            return result

        # Also try by country ISO for AdminBoundary1Unit
        boundary = (
            db.session.query(AdminBoundary1Unit)
            .filter(
                AdminBoundary1Unit.boundaryISO == boundary_id,
                AdminBoundary1Unit.releaseType == release_type,
            )
            .first()
        )

        if boundary:
            result = BoundariesService._format_boundary(boundary)
            result["level"] = 1
            return result

        return None

    @staticmethod
    def get_boundaries_hierarchy(release_type: str = "gbOpen") -> list[dict]:
        """
        Get hierarchical list of boundaries with ADM1 nested under ADM0.

        Returns a complete list without pagination, showing only essential fields.
        ADM1 boundaries are nested under their parent ADM0 country.

        Args:
            release_type: The release type to filter by
                (gbOpen, gbHumanitarian, gbAuthoritative)

        Returns:
            List of ADM0 boundaries with nested ADM1 boundaries
        """
        try:
            # Get all ADM0 boundaries for the specified release type
            adm0_boundaries = (
                db.session.query(AdminBoundary0Metadata)
                .filter(AdminBoundary0Metadata.releaseType == release_type)
                .order_by(AdminBoundary0Metadata.boundaryName)
                .all()
            )

            result = []
            for adm0 in adm0_boundaries:
                # Get all ADM1 units for this country and release type
                adm1_units = (
                    db.session.query(AdminBoundary1Unit)
                    .filter(
                        AdminBoundary1Unit.boundaryISO == adm0.boundaryISO,
                        AdminBoundary1Unit.releaseType == release_type,
                    )
                    .order_by(AdminBoundary1Unit.shapeName)
                    .all()
                )

                # Format ADM1 units with minimal fields
                admin1_list = [
                    {"shapeID": adm1.shapeID, "shapeName": adm1.shapeName}
                    for adm1 in adm1_units
                ]

                # Build hierarchy
                country_data = {
                    "boundaryISO": adm0.boundaryISO,
                    "boundaryName": adm0.boundaryName,
                    "releaseType": adm0.releaseType,
                    "admin1_units": admin1_list,
                }
                result.append(country_data)

            return result

        except Exception as e:
            logger.error(f"Error getting boundaries hierarchy: {str(e)}", exc_info=True)
            raise

    @staticmethod
    def get_last_updated(release_type: str = "gbOpen"):
        """
        Get the most recent modification timestamp across all boundaries.

        Returns the latest updated_at datetime from ADM0, ADM1 metadata,
        and ADM1 unit tables for the specified release type.

        Args:
            release_type: The release type to filter by
                (gbOpen, gbHumanitarian, gbAuthoritative)

        Returns:
            datetime or None if no boundaries exist
        """
        try:
            # Get the most recent updated_at from AdminBoundary0Metadata
            adm0_latest = (
                db.session.query(AdminBoundary0Metadata.updated_at)
                .filter(AdminBoundary0Metadata.releaseType == release_type)
                .order_by(AdminBoundary0Metadata.updated_at.desc())
                .first()
            )

            # Get the most recent updated_at from AdminBoundary1Metadata
            adm1_meta_latest = (
                db.session.query(AdminBoundary1Metadata.updated_at)
                .filter(AdminBoundary1Metadata.releaseType == release_type)
                .order_by(AdminBoundary1Metadata.updated_at.desc())
                .first()
            )

            # Get the most recent updated_at from AdminBoundary1Unit
            adm1_unit_latest = (
                db.session.query(AdminBoundary1Unit.updated_at)
                .filter(AdminBoundary1Unit.releaseType == release_type)
                .order_by(AdminBoundary1Unit.updated_at.desc())
                .first()
            )

            # Compare and return the latest
            timestamps = []
            if adm0_latest and adm0_latest[0]:
                timestamps.append(adm0_latest[0])
            if adm1_meta_latest and adm1_meta_latest[0]:
                timestamps.append(adm1_meta_latest[0])
            if adm1_unit_latest and adm1_unit_latest[0]:
                timestamps.append(adm1_unit_latest[0])

            if timestamps:
                return max(timestamps)
            return None

        except Exception as e:
            logger.error(
                f"Error getting last updated timestamp: {str(e)}", exc_info=True
            )
            raise
