"""
Statistics API endpoints for dashboard data.
Provides comprehensive statistics for executions, users, and system metrics.
"""

import logging
from flask import jsonify, request
from flask_jwt_extended import current_user, jwt_required

from gefapi.routes.api.v1 import endpoints, error
from gefapi.utils.permissions import is_superadmin
from gefapi.services.stats_service import StatsService

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
    
    Returns:
        JSON response with dashboard statistics
    """
    # Only SUPERADMIN users can access dashboard stats
    if not is_superadmin(current_user):
        return error(status=403, detail="SUPERADMIN role required for dashboard statistics")
    
    try:
        # Parse query parameters
        period = request.args.get('period', 'all')
        include_param = request.args.get('include', 'summary,trends,geographic,tasks')
        include = [section.strip() for section in include_param.split(',')]
        
        # Validate period parameter
        valid_periods = ['last_day', 'last_week', 'last_month', 'last_year', 'all']
        if period not in valid_periods:
            return error(status=400, detail=f"Invalid period. Must be one of: {', '.join(valid_periods)}")
        
        # Validate include parameters
        valid_sections = ['summary', 'trends', 'geographic', 'tasks']
        invalid_sections = [section for section in include if section not in valid_sections]
        if invalid_sections:
            return error(status=400, detail=f"Invalid sections: {', '.join(invalid_sections)}. Valid sections: {', '.join(valid_sections)}")
        
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
    - group_by: Grouping interval (hour, day, week, month)
    - task_type: Filter by specific task type
    - status: Filter by execution status (PENDING, RUNNING, FINISHED, FAILED, CANCELLED)
    
    Returns:
        JSON response with execution statistics
    """
    if not is_superadmin(current_user):
        return error(status=403, detail="SUPERADMIN role required for execution statistics")
    
    try:
        # Parse query parameters
        period = request.args.get('period', 'last_month')
        group_by = request.args.get('group_by', 'day')
        task_type = request.args.get('task_type')
        status = request.args.get('status')
        
        # Validate parameters
        valid_periods = ['last_day', 'last_week', 'last_month', 'last_year', 'all']
        if period not in valid_periods:
            return error(status=400, detail=f"Invalid period. Must be one of: {', '.join(valid_periods)}")
        
        valid_group_by = ['hour', 'day', 'week', 'month']
        if group_by not in valid_group_by:
            return error(status=400, detail=f"Invalid group_by. Must be one of: {', '.join(valid_group_by)}")
        
        if status:
            valid_statuses = ['PENDING', 'RUNNING', 'FINISHED', 'FAILED', 'CANCELLED']
            if status not in valid_statuses:
                return error(status=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
        
        # Get statistics from service
        stats = StatsService.get_execution_stats(
            period=period,
            group_by=group_by,
            task_type=task_type,
            status=status
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
    - group_by: Grouping interval (day, week, month)
    - country: Filter by specific country
    
    Returns:
        JSON response with user statistics
    """
    if not is_superadmin(current_user):
        return error(status=403, detail="SUPERADMIN role required for user statistics")
    
    try:
        # Parse query parameters
        period = request.args.get('period', 'last_year')
        group_by = request.args.get('group_by', 'month')
        country = request.args.get('country')
        
        # Validate parameters
        valid_periods = ['last_day', 'last_week', 'last_month', 'last_year', 'all']
        if period not in valid_periods:
            return error(status=400, detail=f"Invalid period. Must be one of: {', '.join(valid_periods)}")
        
        valid_group_by = ['day', 'week', 'month']
        if group_by not in valid_group_by:
            return error(status=400, detail=f"Invalid group_by. Must be one of: {', '.join(valid_group_by)}")
        
        # Get statistics from service
        stats = StatsService.get_user_stats(
            period=period,
            group_by=group_by,
            country=country
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
    """
    if not is_superadmin(current_user):
        return error(status=403, detail="SUPERADMIN role required")
    
    try:
        # Get basic summary for health check
        summary = StatsService._get_summary_stats()
        
        return jsonify(data={
            'status': 'healthy',
            'basic_counts': {
                'total_jobs': summary.get('total_jobs', 0),
                'total_users': summary.get('total_users', 0),
                'jobs_last_month': summary.get('jobs_last_month', 0)
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Error in stats health check: {e}")
        return error(status=500, detail=f"Stats service unhealthy: {str(e)}")


@endpoints.route("/stats/cache", methods=["GET"])
@jwt_required()
def get_cache_info():
    """
    Get information about the stats cache.
    
    Shows cached keys, TTL information, and cache status.
    """
    if not is_superadmin(current_user):
        return error(status=403, detail="SUPERADMIN role required")
    
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
    """
    if not is_superadmin(current_user):
        return error(status=403, detail="SUPERADMIN role required")
    
    try:
        pattern = request.args.get('pattern')
        
        success = StatsService.clear_cache(pattern)
        
        if success:
            message = f"Cache cleared successfully"
            if pattern:
                message += f" for pattern: {pattern}"
            
            return jsonify(data={
                'success': True,
                'message': message
            }), 200
        else:
            return error(status=500, detail="Failed to clear cache")
        
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        return error(status=500, detail=f"Failed to clear cache: {str(e)}")
