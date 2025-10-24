"""
Administrative Boundaries API endpoints.
Provides access to administrative boundary metadata and download URLs from
geoBoundaries dataset. Does NOT return geometries directly - clients should
use the gjDownloadURL to fetch GeoJSON.
"""

import logging

from flask import jsonify, request
from flask_jwt_extended import jwt_required

from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.boundaries_service import BoundariesService

logger = logging.getLogger(__name__)


@endpoints.route("/data/boundaries", methods=["GET"])
@jwt_required()
def get_boundaries():
    """
    Get administrative boundary metadata with download URLs.

    Returns metadata and download URLs from geoBoundaries API responses.
    Clients should use gjDownloadURL to fetch the actual GeoJSON geometry.

    Query Parameters:
    - level: Administrative level (0, 1, or 0,1 for mixed levels, default: 0)
    - release_type: geoBoundaries release type
      (gbOpen, gbHumanitarian, gbAuthoritative, default: gbOpen)
    - id: Filter by ID (boundaryISO for ADM0, shapeID for ADM1)
    - iso: Filter by ISO country code (applies to both levels)
    - name: Filter by name (partial match, case-insensitive) - ONLY works for level=0
      (ADM0). Not supported for level=1 as there are no download links for
      individual admin1 units, only country-level downloads.
    - page: Page number for pagination (default: 1)
    - per_page: Results per page (default: 100, max: 1000)
    - created_at_since: Filter boundaries created since this datetime (ISO format)
    - updated_at_since: Filter boundaries updated since this datetime (ISO format)

    Release Types (as per geoBoundaries API):
    - gbOpen: CC-BY 4.0 compliant, most open license (default)
    - gbHumanitarian: Mirrored from UN OCHA, may have less open licensure
    - gbAuthoritative: Mirrored from UN SALB, verified through in-country
      processes, no commercial use

    Returns:
    - 200: Boundary metadata matching query criteria
    - 400: Invalid query parameters
    - 404: No boundaries found matching criteria

    Example Requests:
    - /api/v1/data/boundaries?level=0&iso=USA
    - /api/v1/data/boundaries?level=0&name=united (name filter only for ADM0)
    - /api/v1/data/boundaries?level=1&iso=USA (ADM1 by country)
    - /api/v1/data/boundaries?level=0,1&iso=USA (mixed levels)
    - /api/v1/data/boundaries?release_type=gbHumanitarian&iso=SYR
    - /api/v1/data/boundaries?release_type=gbAuthoritative&level=0
    - /api/v1/data/boundaries?updated_at_since=2023-01-01T00:00:00Z

    Note: Name filtering is NOT supported for level=1 (ADM1) because geoBoundaries
    only provides country-level downloads for admin1 units, not individual polygon
    downloads. Use the hierarchy endpoint to see admin1 unit names.

    Response includes:
    - boundaryISO: ISO country code
    - releaseType: Release type (gbOpen, gbHumanitarian, or gbAuthoritative)
    - boundaryName: Name of the boundary
    - boundaryType: "ADM0" or "ADM1"
    - Continent: Continent name
    - buildDate: Date the boundary was built (use for updated_at)
    - gjDownloadURL: GeoJSON download URL (use this to fetch geometry)
    - tjDownloadURL: TopoJSON download URL
    - Other geoBoundaries metadata fields
    """
    try:
        # Parse query parameters with validation
        level_param = request.args.get("level", "0")
        release_type = request.args.get("release_type", "gbOpen")

        # Validate release_type
        valid_release_types = ["gbOpen", "gbHumanitarian", "gbAuthoritative"]
        if release_type not in valid_release_types:
            return error(
                400,
                f"Invalid release_type parameter. "
                f"Must be one of: {', '.join(valid_release_types)}",
            )

        # Parse levels - support comma-separated values like "0,1"
        try:
            if "," in level_param:
                levels = [
                    int(level_str.strip()) for level_str in level_param.split(",")
                ]
            else:
                levels = [int(level_param)]

            # Validate level values
            for level in levels:
                if level not in [0, 1]:
                    return error(400, "Invalid level parameter. Must be 0, 1, or 0,1")

        except ValueError:
            return error(400, "Invalid level parameter. Must be 0, 1, or 0,1")
        boundary_id = request.args.get("id")
        iso_code = request.args.get("iso")
        name_filter = request.args.get("name")
        created_at_since = request.args.get("created_at_since")
        updated_at_since = request.args.get("updated_at_since")

        # Parse and validate timestamp parameters
        if created_at_since:
            try:
                from datetime import datetime

                created_at_since = datetime.fromisoformat(
                    created_at_since.replace("Z", "+00:00")
                )
            except ValueError:
                return error(
                    400,
                    "Invalid created_at_since format. Use ISO format "
                    "(e.g., 2023-01-01T00:00:00Z)",
                )

        if updated_at_since:
            try:
                from datetime import datetime

                updated_at_since = datetime.fromisoformat(
                    updated_at_since.replace("Z", "+00:00")
                )
            except ValueError:
                return error(
                    400,
                    "Invalid updated_at_since format. Use ISO format "
                    "(e.g., 2023-01-01T00:00:00Z)",
                )

        # Validate and parse pagination parameters
        try:
            page = max(int(request.args.get("page", 1)), 1)
            per_page = min(int(request.args.get("per_page", 100)), 1000)
        except ValueError:
            return error(400, "Invalid page or per_page parameter")

        # Build filters dictionary
        filters = {}
        if boundary_id:
            filters["boundary_id"] = boundary_id
        if iso_code:
            filters["iso_code"] = iso_code
        if name_filter:
            # Name filtering only supported for ADM0 (level 0)
            # For ADM1, use the hierarchy endpoint to see unit names
            if 1 in levels and 0 not in levels:
                logger.warning(
                    "Name filter requested for level=1 (ADM1) - "
                    "name filtering only supported for level=0. "
                    "Use /hierarchy endpoint to see admin1 unit names."
                )
                return error(
                    400,
                    "Name filtering is not supported for level=1 (ADM1). "
                    "GeoBoundaries only provides country-level downloads for "
                    "admin1 units, not individual polygon downloads. "
                    "Use the /hierarchy endpoint to see admin1 unit names, "
                    "or filter by ISO country code.",
                )
            # Add wildcards for case-insensitive partial matching
            filters["name_filter"] = f"%{name_filter}%"
        if created_at_since:
            filters["created_at_since"] = created_at_since
        if updated_at_since:
            filters["updated_at_since"] = updated_at_since

        # Always filter by release type
        filters["release_type"] = release_type

        # Get boundaries metadata and download URLs
        boundaries, total_count = BoundariesService.get_boundaries(
            levels=levels,
            filters=filters if filters else None,
            format_type="full",  # Kept for backwards compatibility but ignored
            page=page,
            per_page=per_page,
        )

        # Build response with metadata
        # Return 200 with empty data array when no results (RESTful convention)
        response_data = {
            "data": boundaries,
            "meta": {
                "levels": levels,
                "release_type": release_type,
                "total": total_count,
                "page": page,
                "per_page": per_page,
                "has_more": (page * per_page) < total_count,
                "note": "Use gjDownloadURL field to fetch GeoJSON geometry data",
            },
        }

        # Add query info to metadata
        if boundary_id:
            response_data["meta"]["filter_id"] = boundary_id
        if iso_code:
            response_data["meta"]["filter_iso"] = iso_code.upper()
        if name_filter:
            response_data["meta"]["filter_name"] = name_filter

        logger.info(
            f"Boundaries API query successful: levels={levels}, "
            f"release_type={release_type}, results={len(boundaries)}, "
            f"total={total_count}"
        )

        return jsonify(response_data), 200

    except Exception as e:
        logger.error(f"Error in boundaries API: {str(e)}", exc_info=True)
        return error(500, "Internal server error while fetching boundaries")


@endpoints.route("/data/boundaries/stats", methods=["GET"])
@jwt_required()
def get_boundary_statistics():
    """
    Get statistics about available boundary data.

    Returns:
    Statistics including total boundaries per administrative level.
    """
    try:
        stats = BoundariesService.get_boundary_statistics()

        return jsonify({"data": stats}), 200

    except Exception as e:
        logger.error(f"Error getting boundary statistics: {str(e)}", exc_info=True)
        return error(500, "Internal server error while fetching statistics")


@endpoints.route("/data/boundaries/hierarchy", methods=["GET"])
@jwt_required()
def get_boundaries_hierarchy():
    """
    Get hierarchical list of all boundaries with ADM1 nested under ADM0.

    Returns a complete list without pagination, showing only essential fields
    (names and IDs). ADM1 boundaries are nested under their parent ADM0 country.

    Query Parameters:
    - release_type: geoBoundaries release type
      (gbOpen, gbHumanitarian, gbAuthoritative, default: gbOpen)

    Returns:
    - 200: Hierarchical boundary list organized by country
    - 400: Invalid query parameters
    - 500: Server error

    Response Structure:
    {
        "data": [
            {
                "boundaryISO": "USA",
                "boundaryName": "United States",
                "releaseType": "gbOpen",
                "admin1_boundaries": [
                    {
                        "shapeID": "USA-ADM1-CA",
                        "boundaryName": "California"
                    },
                    ...
                ]
            },
            ...
        ]
    }
    """
    try:
        release_type = request.args.get("release_type", "gbOpen")

        # Validate release_type
        valid_release_types = ["gbOpen", "gbHumanitarian", "gbAuthoritative"]
        if release_type not in valid_release_types:
            return error(
                400,
                f"Invalid release_type parameter. "
                f"Must be one of: {', '.join(valid_release_types)}",
            )

        # Get hierarchical boundary list
        hierarchy = BoundariesService.get_boundaries_hierarchy(release_type)

        return jsonify({"data": hierarchy, "meta": {"release_type": release_type}}), 200

    except Exception as e:
        logger.error(f"Error getting boundaries hierarchy: {str(e)}", exc_info=True)
        return error(500, "Internal server error while fetching boundaries hierarchy")


@endpoints.route("/data/boundaries/last-updated", methods=["GET"])
@jwt_required()
def get_boundaries_last_updated():
    """
    Get the most recent modification timestamp across all boundaries.

    Returns the latest updated_at datetime from both ADM0 and ADM1 boundary
    tables. Useful for cache invalidation and determining if boundary data
    needs to be refreshed.

    Query Parameters:
    - release_type: geoBoundaries release type
      (gbOpen, gbHumanitarian, gbAuthoritative, default: gbOpen)

    Returns:
    - 200: Last updated timestamp
    - 400: Invalid query parameters
    - 404: No boundary data found
    - 500: Server error

    Response Structure:
    {
        "data": {
            "last_updated": "2025-01-26T10:30:00.000000",
            "release_type": "gbOpen"
        }
    }
    """
    try:
        release_type = request.args.get("release_type", "gbOpen")

        # Validate release_type
        valid_release_types = ["gbOpen", "gbHumanitarian", "gbAuthoritative"]
        if release_type not in valid_release_types:
            return error(
                400,
                f"Invalid release_type parameter. "
                f"Must be one of: {', '.join(valid_release_types)}",
            )

        # Get last updated timestamp
        last_updated = BoundariesService.get_last_updated(release_type)

        if last_updated is None:
            return error(
                404, f"No boundary data found for release type: {release_type}"
            )

        return (
            jsonify(
                {
                    "data": {
                        "last_updated": last_updated.isoformat()
                        if last_updated
                        else None,
                        "release_type": release_type,
                    }
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error getting last updated timestamp: {str(e)}", exc_info=True)
        return error(500, "Internal server error while fetching last updated timestamp")
