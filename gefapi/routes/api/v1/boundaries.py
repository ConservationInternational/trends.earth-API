"""
Administrative Boundaries API endpoints.
Provides access to administrative boundary data from geoBoundaries dataset.
"""

import logging

from flask import jsonify, request

from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.boundaries_service import BoundariesService

logger = logging.getLogger(__name__)


@endpoints.route("/data/boundaries", methods=["GET"])
def get_boundaries():
    """
    Get administrative boundary data with flexible query options.

    NOTE: This endpoint replaces the deprecated /data/boundaries/countries endpoint.
    For country data only, use: ?level=0&format=table

    Query Parameters:
    - level: Administrative level (0, 1, or 0,1 for mixed levels, default: 0)
    - id: Filter by ID (country code for ADM0, shape_id for ADM1)
    - iso: Filter by ISO country code (applies to both levels)
    - name: Filter by name (partial match, case-insensitive)
    - lat, lon: Filter by point location (requires both parameters)
    - format: Response format ('full' includes geometries, 'table' excludes them)
    - page: Page number for pagination (default: 1)
    - per_page: Results per page (default: 100, max: 1000)
    - created_at_since: Filter boundaries created since this datetime (ISO format)
    - updated_at_since: Filter boundaries updated since this datetime (ISO format)

    Returns:
    - 200: Boundary data matching query criteria
    - 400: Invalid query parameters
    - 404: No boundaries found matching criteria

    Example Requests:
    - /api/v1/data/boundaries?level=0&iso=USA
    - /api/v1/data/boundaries?level=1&iso=USA&name=california
    - /api/v1/data/boundaries?level=0,1&iso=USA (mixed levels)
    - /api/v1/data/boundaries?updated_at_since=2023-01-01T00:00:00Z
    - /api/v1/data/boundaries?lat=40.7128&lon=-74.0060&level=0
    - /api/v1/data/boundaries?format=table&per_page=50
    """
    try:
        # Parse query parameters with validation
        level_param = request.args.get("level", "0")

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
        lat = request.args.get("lat")
        lon = request.args.get("lon")
        response_format = request.args.get("format", "full")
        created_at_since = request.args.get("created_at_since")
        updated_at_since = request.args.get("updated_at_since")

        # Validate response format
        if response_format not in ["full", "table"]:
            return error(400, "Invalid format parameter. Must be 'full' or 'table'")

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

        # Validate coordinate parameters
        if (lat is None) != (lon is None):
            return error(
                400, "Both lat and lon parameters are required for point queries"
            )

        if lat is not None and lon is not None:
            try:
                lat = float(lat)
                lon = float(lon)
                if not BoundariesService.validate_point_coordinates(lat, lon):
                    return error(400, "Invalid coordinate values")
            except ValueError:
                return error(400, "Invalid coordinate format")

        # Build filters dictionary
        filters = {}
        if boundary_id:
            filters["boundary_id"] = boundary_id
        if iso_code:
            filters["iso_code"] = iso_code
        if name_filter:
            filters["name_filter"] = name_filter
        if lat is not None and lon is not None:
            filters["lat"] = lat
            filters["lon"] = lon
        if created_at_since:
            filters["created_at_since"] = created_at_since
        if updated_at_since:
            filters["updated_at_since"] = updated_at_since

        # Use the unified boundaries method for all queries
        boundaries, total_count = BoundariesService.get_boundaries(
            levels=levels,
            filters=filters if filters else None,
            format_type=response_format,
            page=page,
            per_page=per_page,
        )

        if not boundaries:
            return error(404, "No boundaries found matching the specified criteria")

        # Build response with metadata
        response_data = {
            "data": boundaries,
            "meta": {
                "levels": levels,
                "format": response_format,
                "total": total_count,
                "page": page,
                "per_page": per_page,
                "has_more": (page * per_page) < total_count,
            },
        }

        # Add query info to metadata
        if boundary_id:
            response_data["meta"]["filter_id"] = boundary_id
        if iso_code:
            response_data["meta"]["filter_iso"] = iso_code.upper()
        if name_filter:
            response_data["meta"]["filter_name"] = name_filter
        if lat is not None and lon is not None:
            response_data["meta"]["filter_point"] = {"lat": lat, "lon": lon}

        logger.info(
            f"Boundaries API query successful: levels={levels}, "
            f"format={response_format}, results={len(boundaries)}, total={total_count}"
        )

        return jsonify(response_data), 200

    except Exception as e:
        logger.error(f"Error in boundaries API: {str(e)}", exc_info=True)
        return error(500, "Internal server error while fetching boundaries")





@endpoints.route("/data/boundaries/stats", methods=["GET"])
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
