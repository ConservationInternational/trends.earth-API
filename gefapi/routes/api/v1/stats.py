"""
Statistics API endpoints for dashboard data.
Provides comprehensive statistics for executions, users, and system metrics.
"""

import logging

from flask import jsonify, request
from flask_jwt_extended import current_user, jwt_required

from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.stats_service import StatsService
from gefapi.utils.permissions import is_superadmin
from gefapi.utils.security_events import log_admin_action, log_security_event

logger = logging.getLogger(__name__)


@endpoints.route("/stats/dashboard", methods=["GET"])
@jwt_required()
def get_dashboard_stats():
    """
    Get comprehensive dashboard statistics.

    Only SUPERADMIN users can access dashboard statistics.

    Query Parameters:
    - period: Time period filter (last_day, last_week, last_month, last_year, all)
    - include: Comma-separated list of sections (summary, trends, geographic, tasks)

    Example Response for include=summary (period=all):
    {
        "data": {
            "summary": {
                "total_executions": 1500,
                "total_jobs": 1500,
                "total_users": 250,
                "total_scripts": 150,
                "total_executions_finished": 1200,
                "total_executions_failed": 200,
                "total_executions_cancelled": 100
            }
        }
    }

    Example Response for include=summary (period=last_month):
    {
        "data": {
            "summary": {
                "total_executions": 450,
                "total_jobs": 450,
                "total_users": 80,
                "total_scripts": 150,
                "total_executions_finished": 360,
                "total_executions_failed": 60,
                "total_executions_cancelled": 30
            }
        }
    }

    Example Response for include=trends:
    {
        "data": {
            "trends": {
                "hourly_jobs": [
                    {"hour": "2025-08-08T10:00:00Z", "count": 5},
                    {"hour": "2025-08-08T11:00:00Z", "count": 8}
                ],
                "daily_jobs": [
                    {"date": "2025-08-07", "count": 45},
                    {"date": "2025-08-08", "count": 52}
                ],
                "monthly_jobs": [
                    {"month": "2025-07", "count": 1200},
                    {"month": "2025-08", "count": 300}
                ]
            }
        }
    }

    Example Response for include=geographic:
    {
        "data": {
            "geographic": {
                "countries": {
                    "USA": 100,
                    "Brazil": 75,
                    "Germany": 50
                },
                "total_users": 225
            }
        }
    }

    Example Response for include=tasks:
    {
        "data": {
            "tasks": {
                "by_type": [
                    {"task": "productivity", "count": 500, "success_rate": 95.0},
                    {"task": "land-cover", "count": 300, "success_rate": 92.0},
                    {"task": "carbon", "count": 200, "success_rate": 89.0}
                ],
                "by_version": [
                    {"version": "2", "count": 600, "percentage": 60.0},
                    {"version": "1", "count": 400, "percentage": 40.0}
                ]
            }
        }
    }

    Example Response for include=summary,trends,geographic,tasks (all sections):
    {
        "data": {
            "summary": { ... },
            "trends": { ... },
            "geographic": { ... },
            "tasks": { ... }
        }
    }

    Returns:
        JSON response with dashboard statistics containing requested sections
    """
    # Only SUPERADMIN users can access dashboard stats
    if not is_superadmin(current_user):
        log_security_event(
            "UNAUTHORIZED_ACCESS",
            user_id=str(current_user.id),
            user_email=current_user.email,
            details={
                "attempted_endpoint": "/stats/dashboard",
                "reason": "insufficient_privileges",
            },
            level="warning",
        )
        return error(
            status=403, detail="SUPERADMIN role required for dashboard statistics"
        )

    # Log admin action for audit trail
    log_admin_action(
        admin_user_id=str(current_user.id),
        admin_email=current_user.email,
        action="accessed_dashboard_stats",
        target_user_id=None,
    )

    try:
        # Parse query parameters
        period = request.args.get("period", "all")
        include_param = request.args.get("include", "summary,trends,geographic,tasks")
        include = [section.strip() for section in include_param.split(",")]

        # Validate period parameter
        valid_periods = ["last_day", "last_week", "last_month", "last_year", "all"]
        if period not in valid_periods:
            return error(
                status=400,
                detail=f"Invalid period. Must be one of: {', '.join(valid_periods)}",
            )

        # Validate include parameters
        valid_sections = ["summary", "trends", "geographic", "tasks"]
        invalid_sections = [
            section for section in include if section not in valid_sections
        ]
        if invalid_sections:
            return error(
                status=400,
                detail=(
                    f"Invalid sections: {', '.join(invalid_sections)}. "
                    f"Valid sections: {', '.join(valid_sections)}"
                ),
            )

        # Get statistics from service
        stats = StatsService.get_dashboard_stats(period=period, include=include)

        return jsonify(data=stats), 200

    except Exception as e:
        logger.error(f"Error getting dashboard stats: {e}")
        return error(status=500, detail=f"Failed to get dashboard stats: {str(e)}")


@endpoints.route("/stats/executions", methods=["GET"])
@jwt_required()
def get_execution_stats():
    """
    Get execution statistics and trends.

    Only SUPERADMIN users can access execution statistics.

    Query Parameters:
    - period: Time period filter (last_day, last_week, last_month, last_year, all)
    - group_by: Grouping interval (quarter_hour, hour, day, week, month)
    - task_type: Filter by specific task type
    - status: Filter by execution status (PENDING, RUNNING, FINISHED, FAILED, CANCELLED)

    Example Response:
    {
        "data": {
            "time_series": [
                {
                    "timestamp": "2025-08-08T10:00:00Z",
                    "total": 10,
                    "by_status": {"FINISHED": 8, "FAILED": 2},
                    "by_task": {"productivity": 6, "land-cover": 4}
                }
            ],
            "top_users": [
                {
                    "user_id": "123",
                    "email": "user1@example.com",
                    "execution_count": 45,
                    "success_rate": 94.4,
                    "favorite_tasks": ["productivity", "land-cover"]
                }
            ],
            "task_performance": [
                {
                    "task": "productivity",
                    "total_executions": 500,
                    "success_rate": 95.0,
                    "avg_duration_minutes": 45.2,
                    "failure_reasons": []
                }
            ]
        }
    }

    Returns:
        JSON response with execution statistics
    """
    if not is_superadmin(current_user):
        log_security_event(
            "UNAUTHORIZED_ACCESS",
            user_id=str(current_user.id),
            user_email=current_user.email,
            details={
                "attempted_endpoint": "/stats/executions",
                "reason": "insufficient_privileges",
            },
            level="warning",
        )
        return error(
            status=403, detail="SUPERADMIN role required for execution statistics"
        )

    # Log admin action for audit trail
    log_admin_action(
        admin_user_id=str(current_user.id),
        admin_email=current_user.email,
        action="accessed_execution_stats",
        target_user_id=None,
    )

    try:
        # Parse query parameters
        period = request.args.get("period", "last_month")
        group_by = request.args.get("group_by", "day")
        task_type = request.args.get("task_type")
        status = request.args.get("status")

        # Validate parameters
        valid_periods = ["last_day", "last_week", "last_month", "last_year", "all"]
        if period not in valid_periods:
            return error(
                status=400,
                detail=f"Invalid period. Must be one of: {', '.join(valid_periods)}",
            )

        valid_group_by = ["quarter_hour", "hour", "day", "week", "month"]
        if group_by not in valid_group_by:
            return error(
                status=400,
                detail=f"Invalid group_by. Must be one of: {', '.join(valid_group_by)}",
            )

        if status:
            valid_statuses = ["PENDING", "RUNNING", "FINISHED", "FAILED", "CANCELLED"]
            if status not in valid_statuses:
                return error(
                    status=400,
                    detail=(
                        f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
                    ),
                )

        # Get statistics from service
        stats = StatsService.get_execution_stats(
            period=period, group_by=group_by, task_type=task_type, status=status
        )

        return jsonify(data=stats), 200

    except Exception as e:
        logger.error(f"Error getting execution stats: {e}")
        return error(status=500, detail=f"Failed to get execution stats: {str(e)}")


@endpoints.route("/stats/users", methods=["GET"])
@jwt_required()
def get_user_stats():
    """
    Get user statistics and geographical distribution.

    Only SUPERADMIN users can access user statistics.

    Query Parameters:
    - period: Time period filter (last_day, last_week, last_month, last_year, all)
    - group_by: Grouping interval (quarter_hour, hour, day, week, month)
    - country: Filter by specific country

    Example Response:
    {
        "data": {
            "registration_trends": [
                {"date": "2025-08-07", "new_users": 12, "total_users": 250},
                {"date": "2025-08-08", "new_users": 8, "total_users": 258}
            ],
            "geographic_distribution": [
                {"country": "USA", "user_count": 100, "percentage": 40.0},
                {"country": "Brazil", "user_count": 75, "percentage": 30.0},
                {"country": "Germany", "user_count": 50, "percentage": 20.0}
            ],
            "activity_stats": {
                "active_last_day": 45,
                "active_last_week": 234,
                "active_last_month": 567,
                "never_executed": 123
            }
        }
    }

    Returns:
        JSON response with user statistics
    """
    if not is_superadmin(current_user):
        log_security_event(
            "UNAUTHORIZED_ACCESS",
            user_id=str(current_user.id),
            user_email=current_user.email,
            details={
                "attempted_endpoint": "/stats/users",
                "reason": "insufficient_privileges",
            },
            level="warning",
        )
        return error(status=403, detail="SUPERADMIN role required for user statistics")

    # Log admin action for audit trail - this includes PII access
    log_admin_action(
        admin_user_id=str(current_user.id),
        admin_email=current_user.email,
        action="accessed_user_stats",
        target_user_id=None,
    )

    # Log data export event since this includes user information
    log_security_event(
        "DATA_EXPORT",
        user_id=str(current_user.id),
        user_email=current_user.email,
        details={"data_type": "user_statistics", "endpoint": "/stats/users"},
        level="info",
    )

    try:
        # Parse query parameters
        period = request.args.get("period", "last_year")
        raw_group_by = request.args.get("group_by", "month")
        group_by = StatsService.normalize_user_group_by(raw_group_by)
        country = request.args.get("country")

        # Validate parameters
        valid_periods = ["last_day", "last_week", "last_month", "last_year", "all"]
        if period not in valid_periods:
            return error(
                status=400,
                detail=f"Invalid period. Must be one of: {', '.join(valid_periods)}",
            )

        valid_group_by = ["quarter_hour", "hour", "day", "week", "month"]
        if group_by not in valid_group_by:
            return error(
                status=400,
                detail=(
                    f"Invalid group_by. Must be one of: {', '.join(valid_group_by)}"
                ),
            )

        # Get statistics from service
        stats = StatsService.get_user_stats(
            period=period, group_by=group_by, country=country
        )

        return jsonify(data=stats), 200

    except Exception as e:
        logger.error(f"Error getting user stats: {e}")
        return error(status=500, detail=f"Failed to get user stats: {str(e)}")


@endpoints.route("/stats/health", methods=["GET"])
@jwt_required()
def get_stats_health():
    """
    Get basic health check for stats endpoints.

    Returns basic counts to verify the stats service is working.

    Example Response:
    {
        "data": {
            "status": "healthy",
            "basic_counts": {
                "total_jobs": 1500,
                "total_users": 250,
                "jobs_last_month": 450
            }
        }
    }

    Returns:
        JSON response with health status and basic counts
    """
    if not is_superadmin(current_user):
        log_security_event(
            "UNAUTHORIZED_ACCESS",
            user_id=str(current_user.id),
            user_email=current_user.email,
            details={
                "attempted_endpoint": "/stats/health",
                "reason": "insufficient_privileges",
            },
            level="warning",
        )
        return error(status=403, detail="SUPERADMIN role required")

    try:
        # Get basic summary for health check
        summary = StatsService._get_summary_stats()

        return jsonify(
            data={
                "status": "healthy",
                "basic_counts": {
                    "total_jobs": summary.get("total_jobs", 0),
                    "total_users": summary.get("total_users", 0),
                    "jobs_last_month": summary.get("jobs_last_month", 0),
                },
            }
        ), 200

    except Exception as e:
        logger.error(f"Error in stats health check: {e}")
        return error(status=500, detail=f"Stats service unhealthy: {str(e)}")


@endpoints.route("/stats/cache", methods=["GET"])
@jwt_required()
def get_cache_info():
    """
    Get information about the stats cache.

    Shows cached keys, TTL information, and cache status.

    Example Response:
    {
        "data": {
            "cache_status": "available",
            "total_keys": 15,
            "keys": [
                {
                    "key": "stats_service:get_dashboard_stats:include=summary"
                           "_period=all",
                    "ttl": 240,
                    "size_bytes": 1024
                }
            ],
            "memory_usage": {
                "used_memory": "2.5MB",
                "max_memory": "256MB"
            }
        }
    }

    Returns:
        JSON response with cache information
    """
    if not is_superadmin(current_user):
        log_security_event(
            "UNAUTHORIZED_ACCESS",
            user_id=str(current_user.id),
            user_email=current_user.email,
            details={
                "attempted_endpoint": "/stats/cache",
                "reason": "insufficient_privileges",
            },
            level="warning",
        )
        return error(status=403, detail="SUPERADMIN role required")

    # Log admin action
    log_admin_action(
        admin_user_id=str(current_user.id),
        admin_email=current_user.email,
        action="accessed_cache_info",
        target_user_id=None,
    )

    try:
        cache_info = StatsService.get_cache_info()
        return jsonify(data=cache_info), 200

    except Exception as e:
        logger.error(f"Error getting cache info: {e}")
        return error(status=500, detail=f"Failed to get cache info: {str(e)}")


@endpoints.route("/stats/cache", methods=["DELETE"])
@jwt_required()
def clear_cache():
    """
    Clear the stats cache.

    Query Parameters:
    - pattern: Optional pattern to match cache keys (e.g., 'summary', 'trends')

    Allowed patterns: summary, trends, geographic, tasks, executions, users, activity

    Example Response (success):
    {
        "data": {
            "success": true,
            "message": "Cache cleared successfully"
        }
    }

    Example Response (with pattern):
    {
        "data": {
            "success": true,
            "message": "Cache cleared successfully for pattern: summary"
        }
    }

    Returns:
        JSON response indicating success or failure of cache clearing operation
    """
    if not is_superadmin(current_user):
        log_security_event(
            "UNAUTHORIZED_ACCESS",
            user_id=str(current_user.id),
            user_email=current_user.email,
            details={
                "attempted_endpoint": "/stats/cache",
                "method": "DELETE",
                "reason": "insufficient_privileges",
            },
            level="warning",
        )
        return error(status=403, detail="SUPERADMIN role required")

    pattern = request.args.get("pattern")

    # Validate pattern to prevent malicious cache clearing
    if pattern:
        allowed_patterns = [
            "summary",
            "trends",
            "geographic",
            "tasks",
            "executions",
            "users",
            "activity",
        ]
        if pattern not in allowed_patterns:
            log_security_event(
                "SUSPICIOUS_ACTIVITY",
                user_id=str(current_user.id),
                user_email=current_user.email,
                details={
                    "action": "invalid_cache_pattern",
                    "pattern": pattern,
                    "endpoint": "/stats/cache",
                },
                level="warning",
            )
            return error(
                status=400,
                detail=(
                    f"Invalid pattern. Allowed patterns: {', '.join(allowed_patterns)}"
                ),
            )

    # Log admin action
    log_admin_action(
        admin_user_id=str(current_user.id),
        admin_email=current_user.email,
        action="cleared_stats_cache",
        target_user_id=None,
    )

    try:
        success = StatsService.clear_cache(pattern)

        if success:
            message = "Cache cleared successfully"
            if pattern:
                message += f" for pattern: {pattern}"

            return jsonify(data={"success": True, "message": message}), 200
        return error(status=500, detail="Failed to clear cache")

    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        return error(status=500, detail=f"Failed to clear cache: {str(e)}")
