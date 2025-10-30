"""System monitoring and status routes for the Trends.Earth API."""

import datetime
import logging

import dateutil.parser
from flask import jsonify, request
from flask_jwt_extended import current_user, get_jwt_identity, jwt_required

from gefapi.routes.api.v1 import endpoints, error
from gefapi.services import StatusService, UserService
from gefapi.utils.permissions import can_access_admin_features

logger = logging.getLogger()


@endpoints.route("/status", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_status_logs():
    """
    Retrieve system status logs for monitoring and diagnostics.

    **Authentication**: JWT token required
    **Access**: Restricted to ADMIN and SUPERADMIN users only
    **Purpose**: Monitor system health, track events, and diagnose issues

    **Query Parameters**:
    - `start_date`: Filter logs from this date onwards (ISO 8601 format)
    - `end_date`: Filter logs up to this date (ISO 8601 format)
    - `sort`: Sort field (prefix with '-' for descending, e.g., '-timestamp')
    - `page`: Page number for pagination (default: 1)
    - `per_page`: Items per page (1-10000, default: 100)

    **Response Schema**:
    ```json
    {
      "data": [
        {
          "id": 123,
          "timestamp": "2025-01-15T10:30:00Z",
          "executions_pending": 2,
          "executions_ready": 2,
          "executions_running": 3,
          "executions_finished": 8,
          "executions_failed": 1,
          "executions_cancelled": 0
        },
        {
          "id": 124,
          "timestamp": "2025-01-15T10:35:00Z",
          "executions_pending": 3,
          "executions_ready": 5,
          "executions_running": 3,
          "executions_finished": 12,
          "executions_failed": 2,
          "executions_cancelled": 1
        }
      ],
      "page": 1,
      "per_page": 100,
      "total": 1547
    }
    ```

    **Status Log Fields**:
    - `id`: Unique identifier for the status log entry
    - `timestamp`: When the status was recorded (ISO 8601 format)
    - `executions_pending`: Number of executions queued to start (PENDING state)
    - `executions_ready`: Number of executions in READY state
    - `executions_running`: Number of currently running executions
    - `executions_finished`: Number of executions that finished
    - `executions_failed`: Number of executions that failed
    - `executions_cancelled`: Number of executions that were cancelled

    **Monitoring Metrics**:
    - Track execution queue length and processing status
    - Monitor execution completion and failure rates
    - Identify trends in script execution success/failure rates
    - System health indicators for capacity planning
    - Event-driven status tracking provides real-time execution state

    **Date Filtering Examples**:
    - `?start_date=2025-01-15T00:00:00Z` - Logs from January 15th onwards
    - `?end_date=2025-01-15T23:59:59Z` - Logs up to end of January 15th
    - `?start_date=2025-01-10T00:00:00Z&end_date=2025-01-15T23:59:59Z` - Logs
      within date range

    **Sorting Examples**:
    - `?sort=timestamp` - Chronological order (oldest first)
    - `?sort=-timestamp` - Reverse chronological (newest first, default)
    - `?sort=level` - Sort by severity level

    **Pagination Examples**:
    - `?page=1&per_page=50` - First 50 entries
    - `?page=2&per_page=100` - Next 100 entries
    - Default pagination: 100 items per page

    **Use Cases**:
    - Monitor execution queue length and processing capacity
    - Track system growth (users and scripts over time)
    - Analyze execution success rates and failure patterns
    - Capacity planning based on execution activity trends
    - Performance monitoring and bottleneck identification

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Insufficient privileges (ADMIN+ required)
    - `500 Internal Server Error`: Failed to retrieve status logs
    """
    logger.info("[ROUTER]: Getting status logs")

    # Check if user is admin or higher
    identity = current_user
    if not can_access_admin_features(identity):
        return error(status=403, detail="Forbidden")

    period = request.args.get("period")
    if period:
        valid_periods = ["last_day", "last_week", "last_month", "last_year", "all"]
        if period not in valid_periods:
            return error(
                status=400,
                detail=f"Invalid period. Must be one of: {', '.join(valid_periods)}",
            )

    aggregate_param = request.args.get("aggregate", "false").lower()
    aggregate = aggregate_param in {"true", "1", "yes"}
    group_by = request.args.get("group_by")
    if group_by:
        group_by = group_by.lower()

    # Parse date filters
    start_date_raw = request.args.get("start_date")
    start_date = dateutil.parser.parse(start_date_raw) if start_date_raw else None

    end_date_raw = request.args.get("end_date")
    end_date = dateutil.parser.parse(end_date_raw) if end_date_raw else None

    if period:
        period_start, period_end = StatusService.resolve_period_bounds(period)
        if period_start and start_date is None:
            start_date = period_start
        if period_end and end_date is None:
            end_date = period_end

    if aggregate:
        if not group_by:
            return error(
                status=400,
                detail="group_by parameter is required when aggregate=true",
            )

        valid_group_by = {"hour", "day", "week", "month"}
        if group_by not in valid_group_by:
            return error(
                status=400,
                detail=(
                    "Invalid group_by. Must be one of: "
                    f"{', '.join(sorted(valid_group_by))}"
                ),
            )

    # Parse sorting
    sort = request.args.get("sort")

    # Parse pagination
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 100))
        page = max(page, 1)
        per_page = min(max(per_page, 1), 10000)
    except ValueError:
        page, per_page = 1, 100

    if aggregate:
        grouped_logs = StatusService.get_status_logs_grouped(
            group_by=group_by,
            start_date=start_date,
            end_date=end_date,
            sort=sort,
        )

        total = len(grouped_logs)
        return (
            jsonify(
                data=grouped_logs,
                page=1,
                per_page=total,
                total=total,
            ),
            200,
        )

    try:
        status_logs, total = StatusService.get_status_logs(
            start_date=start_date,
            end_date=end_date,
            sort=sort,
            page=page,
            per_page=per_page,
        )
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")

    return (
        jsonify(
            data=[status_log.serialize() for status_log in status_logs],
            page=page,
            per_page=per_page,
            total=total,
        ),
        200,
    )


@endpoints.route("/status/swarm", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_swarm_status():
    """
    Get cached Docker Swarm cluster status including comprehensive node information.

    **Authentication**: JWT token required
    **Access**: Restricted to ADMIN and SUPERADMIN users only
    **Purpose**: Monitor Docker Swarm health, node resources, and capacity
    **Performance**: Uses Redis-cached data updated every 2 minutes for fast response

    **Response Schema**:
    ```json
    {
      "data": {
        "swarm_active": true,
        "total_nodes": 3,
        "total_managers": 1,
        "total_workers": 2,
        "error": null,
        "cache_info": {
          "cached_at": "2025-01-15T10:30:00Z",
          "cache_ttl": 300,
          "cache_key": "docker_swarm_status",
          "source": "cached"
        },
        "nodes": [
          {
            "id": "node-id-123",
            "hostname": "manager-01",
            "role": "manager",
            "is_manager": true,
            "is_leader": true,
            "availability": "active",
            "state": "ready",
            "cpu_count": 4.0,
            "memory_gb": 8.0,
            "running_tasks": 3,
            "available_capacity": 37,
            "resource_usage": {
              "used_cpu_nanos": 300000000,
              "used_memory_bytes": 536870912,
              "available_cpu_nanos": 3700000000,
              "available_memory_bytes": 7548381184,
              "used_cpu_percent": 7.5,
              "used_memory_percent": 6.25
            },
            "labels": {"node.role": "manager"},
            "created_at": "2025-01-15T10:00:00Z",
            "updated_at": "2025-01-15T10:30:00Z"
          }
        ]
      }
    }
    ```

    **Data Source**:
    - Uses cached Docker Swarm data from Redis (updated every 2 minutes)
    - Resource calculations based on actual Docker Swarm task reservations
    - Node capacity calculated from CPU/memory resources and current task load

    **Error Responses**:
    - 403: Access denied (non-admin user)
    - 500: Server error

    **Note**: When Docker is not in swarm mode or unavailable, returns:
    ```json
    {
      "data": {
        "swarm_active": false,
        "error": "Not in swarm mode" | "Docker unavailable",
        "nodes": [],
        "total_nodes": 0,
        "total_managers": 0,
        "total_workers": 0,
        "cache_info": {
          "cached_at": "2025-01-15T10:30:00Z",
          "cache_ttl": 0,
          "cache_key": "docker_swarm_status",
          "source": "real_time_fallback" | "endpoint_error_fallback"
        }
      }
    }
    ```
    """
    logger.info("[ROUTER]: Getting Docker Swarm status")

    try:
        # Check user permissions
        user_id = get_jwt_identity()
        user = UserService.get_user(user_id)

        if not user or user.role not in ["ADMIN", "SUPERADMIN"]:
            logger.error(f"[ROUTER]: Access denied for user {user_id}")
            return error(status=403, detail="Access denied. Admin privileges required.")

        # Get cached Docker Swarm information (fast)
        try:
            from gefapi.tasks.status_monitoring import get_cached_swarm_status

            swarm_info = get_cached_swarm_status()
        except Exception as swarm_error:
            logger.warning(
                f"[ROUTER]: Failed to get cached Docker Swarm info: {swarm_error}"
            )
            swarm_info = {
                "error": f"Cache retrieval failed: {str(swarm_error)}",
                "nodes": [],
                "total_nodes": 0,
                "total_managers": 0,
                "total_workers": 0,
                "swarm_active": False,
                "cache_info": {
                    "cached_at": datetime.datetime.now(datetime.UTC).isoformat(),
                    "cache_ttl": 0,
                    "cache_key": "docker_swarm_status",
                    "source": "endpoint_error_fallback",
                },
            }

        logger.info("[ROUTER]: Successfully retrieved swarm status")
        return jsonify(data=swarm_info), 200

    except Exception as e:
        logger.error(f"[ROUTER]: Error getting swarm status: {str(e)}")
        return error(status=500, detail="Error retrieving swarm status")
