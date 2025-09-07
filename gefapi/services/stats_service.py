"""
Statistics service for dashboard data aggregation.
Provides comprehensive statistics for executions, users, and system metrics.
"""

from datetime import datetime, timedelta
import logging
from typing import Any

from sqlalchemy import desc, func

from gefapi import db
from gefapi.models import Execution, Script, User
from gefapi.utils.redis_cache import get_redis_cache

logger = logging.getLogger(__name__)


class StatsService:
    """Service for generating dashboard statistics with Redis caching."""

    # Cache TTL in seconds (5 minutes)
    CACHE_TTL = 300

    @staticmethod
    def _get_cache_key(method_name: str, **kwargs) -> str:
        """
        Generate a consistent cache key for a method call.

        Args:
            method_name: Name of the method being cached
            **kwargs: Method parameters to include in cache key

        Returns:
            str: Formatted cache key with sorted parameters for consistency
        """
        # Sort kwargs for consistent cache keys
        sorted_kwargs = sorted(kwargs.items())
        kwargs_str = "_".join([f"{k}={v}" for k, v in sorted_kwargs])
        return f"stats_service:{method_name}:{kwargs_str}"

    @staticmethod
    def _get_from_cache_or_execute(cache_key: str, execution_func) -> Any:
        """
        Get data from Redis cache or execute function and cache the result.

        Implements caching pattern with fallback to direct execution when Redis
        is unavailable. Automatically handles cache misses and sets cache values.

        Args:
            cache_key: Redis key for caching the result
            execution_func: Function to execute if cache miss or Redis unavailable

        Returns:
            Any: Cached result or fresh execution result

        Raises:
            Exception: Re-raises any exception from the execution function
        """
        redis_cache = get_redis_cache()
        # Try to get from cache first
        if redis_cache.is_available():
            cached_result = redis_cache.get(cache_key)
            if cached_result is not None:
                logger.debug(f"Retrieved stats from cache: {cache_key}")
                return cached_result

        # Execute function and cache result
        try:
            result = execution_func()

            # Cache the result if Redis is available
            if redis_cache.is_available():
                if redis_cache.set(cache_key, result, ttl=StatsService.CACHE_TTL):
                    logger.debug(f"Cached stats result: {cache_key}")
                else:
                    logger.warning(f"Failed to cache stats result: {cache_key}")

            return result

        except Exception as e:
            logger.error(
                f"Error executing stats function for cache key {cache_key}: {e}"
            )
            raise

    @staticmethod
    def get_dashboard_stats(
        period: str = "all", include: list[str] | None = None
    ) -> dict[str, Any]:
        """
        Get comprehensive dashboard statistics.

        Args:
            period: Time period filter (last_day, last_week, last_month, last_year, all)
                   Affects all sections:
                   - summary: Filters all counts to the selected period only
                   - trends: Filters time series data based on the period
                   - geographic: Filters user registration data based on the period
                   - tasks: Filters execution data based on the period
            include: List of sections to include (summary, trends, geographic, tasks)

        Returns:
            Dictionary containing requested statistics sections
        """
        if include is None:
            include = ["summary", "trends", "geographic", "tasks"]

        # Generate cache key
        cache_key = StatsService._get_cache_key(
            "get_dashboard_stats", period=period, include=",".join(sorted(include))
        )

        def execute_stats():
            result = {}

            try:
                if "summary" in include:
                    result["summary"] = StatsService._get_summary_stats(period)

                if "trends" in include:
                    result["trends"] = StatsService._get_trends_data(period)

                if "geographic" in include:
                    result["geographic"] = StatsService._get_geographic_data(period)

                if "tasks" in include:
                    result["tasks"] = StatsService._get_task_stats(period)

                return result

            except Exception as e:
                logger.error(f"Error generating dashboard stats: {e}")
                raise

        return StatsService._get_from_cache_or_execute(cache_key, execute_stats)

    @staticmethod
    def get_execution_stats(
        period: str = "last_month",
        group_by: str = "day",
        task_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """
        Get detailed execution statistics and trends.

        Args:
            period: Time period filter
            group_by: Grouping interval (hour, day, week, month)
            task_type: Filter by specific task type
            status: Filter by execution status
        Returns:
            Dictionary containing execution statistics
        """
        # Generate cache key
        cache_key = StatsService._get_cache_key(
            "get_execution_stats",
            period=period,
            group_by=group_by,
            task_type=task_type or "none",
            status=status or "none",
        )

        def execute_stats():
            try:
                result = {
                    "time_series": StatsService._get_execution_time_series(
                        period, group_by, task_type, status
                    ),
                    "top_users": StatsService._get_top_users(period, task_type),
                    "task_performance": StatsService._get_task_performance(period),
                }

                return result

            except Exception as e:
                logger.error(f"Error generating execution stats: {e}")
                raise

        return StatsService._get_from_cache_or_execute(cache_key, execute_stats)

    @staticmethod
    def get_user_stats(
        period: str = "last_year",
        group_by: str = "month",
        country: str | None = None,
    ) -> dict[str, Any]:
        """
        Get user statistics and geographical distribution.

        Args:
            period: Time period filter
            group_by: Grouping interval
            country: Filter by specific country
        Returns:
            Dictionary containing user statistics
        """
        # Generate cache key
        cache_key = StatsService._get_cache_key(
            "get_user_stats",
            period=period,
            group_by=group_by,
            country=country or "none",
        )

        def execute_stats():
            try:
                result = {
                    "registration_trends": StatsService._get_registration_trends(
                        period, group_by, country
                    ),
                    "geographic_distribution": StatsService._get_geographic_data(
                        period
                    ),
                    "activity_stats": StatsService._get_activity_stats(),
                }

                return result

            except Exception as e:
                logger.error(f"Error generating user stats: {e}")
                raise

        return StatsService._get_from_cache_or_execute(cache_key, execute_stats)

    @staticmethod
    def _get_time_filter(period: str) -> datetime | None:
        """
        Get datetime cutoff for filtering data by time period.

        Args:
            period: Time period string ('last_day', 'last_week', 'last_month',
                   'last_year', or any other value for no filter)

        Returns:
            Optional[datetime]: Cutoff datetime for the period, or None for
                'all'/'invalid'
        """
        now = datetime.utcnow()

        filters = {
            "last_day": now - timedelta(days=1),
            "last_week": now - timedelta(days=7),
            "last_month": now - timedelta(days=30),
            "last_year": now - timedelta(days=365),
        }

        return filters.get(period)

    @staticmethod
    def _get_summary_stats(period: str = "all") -> dict[str, Any]:
        """
        Get comprehensive summary statistics for the dashboard.

        Args:
            period: Time period filter for filtering execution and user data
                   (last_day, last_week, last_month, last_year, all)

        Returns summary counts for the specified period:
        - Total executions, users, and scripts (filtered by period if not 'all')
        - Execution counts by status (filtered by period if not 'all')

        Returns:
            dict: Summary statistics containing:
                - total_executions: Total number of executions (within period)
                - total_jobs: Alias for total_executions (backward compatibility)
                - total_users: Total number of registered users (within period)
                - total_scripts: Total number of available scripts (always all-time)
                - total_executions_finished: Count of successfully completed executions
                - total_executions_failed: Count of failed executions
                - total_executions_cancelled: Count of cancelled executions
        """
        cache_key = StatsService._get_cache_key("_get_summary_stats", period=period)

        def execute_summary():
            # Get period filter
            cutoff_date = StatsService._get_time_filter(period)

            # Get total counts (filtered by period if specified)
            execution_query = db.session.query(func.count(Execution.id))
            user_query = db.session.query(func.count(User.id))

            if cutoff_date:
                execution_query = execution_query.filter(
                    Execution.start_date >= cutoff_date
                )
                user_query = user_query.filter(User.created_at >= cutoff_date)

            total_executions = execution_query.scalar() or 0
            total_users = user_query.scalar() or 0
            # Scripts count is always all-time since scripts don't have a time dimension
            total_scripts = db.session.query(func.count(Script.id)).scalar() or 0

            # Get execution counts by status (filtered by period if specified)
            finished_query = db.session.query(func.count(Execution.id)).filter(
                Execution.status == "FINISHED"
            )
            failed_query = db.session.query(func.count(Execution.id)).filter(
                Execution.status == "FAILED"
            )
            cancelled_query = db.session.query(func.count(Execution.id)).filter(
                Execution.status == "CANCELLED"
            )

            if cutoff_date:
                finished_query = finished_query.filter(
                    Execution.start_date >= cutoff_date
                )
                failed_query = failed_query.filter(Execution.start_date >= cutoff_date)
                cancelled_query = cancelled_query.filter(
                    Execution.start_date >= cutoff_date
                )

            total_executions_finished = finished_query.scalar() or 0
            total_executions_failed = failed_query.scalar() or 0
            total_executions_cancelled = cancelled_query.scalar() or 0

            summary = {
                "total_executions": total_executions,
                "total_jobs": total_executions,  # Keep for backward compatibility
                "total_users": total_users,
                "total_scripts": total_scripts,
                "total_executions_finished": total_executions_finished,
                "total_executions_failed": total_executions_failed,
                "total_executions_cancelled": total_executions_cancelled,
            }

            return summary

        return StatsService._get_from_cache_or_execute(cache_key, execute_summary)

    @staticmethod
    def _get_trends_data(period: str) -> dict[str, Any]:
        """Get trend data for jobs over time."""
        cache_key = StatsService._get_cache_key("_get_trends_data", period=period)

        def execute_trends():
            cutoff_date = StatsService._get_time_filter(period)

            trends = {}

            # Hourly data (last 72 hours)
            if period in ["last_day", "all"] or cutoff_date is None:
                hourly_cutoff = datetime.utcnow() - timedelta(hours=72)
                hourly_data = (
                    db.session.query(
                        func.date_trunc("hour", Execution.start_date).label("hour"),
                        func.count(Execution.id).label("count"),
                    )
                    .filter(Execution.start_date >= hourly_cutoff)
                    .group_by(func.date_trunc("hour", Execution.start_date))
                    .order_by("hour")
                    .all()
                )

                trends["hourly_jobs"] = [
                    {
                        "hour": row.hour.isoformat() if row.hour else None,
                        "count": row.count,
                    }
                    for row in hourly_data
                ]

            # Daily data
            if cutoff_date:
                daily_data = (
                    db.session.query(
                        func.date_trunc("day", Execution.start_date).label("date"),
                        func.count(Execution.id).label("count"),
                    )
                    .filter(Execution.start_date >= cutoff_date)
                    .group_by(func.date_trunc("day", Execution.start_date))
                    .order_by("date")
                    .all()
                )

                trends["daily_jobs"] = [
                    {
                        "date": row.date.date().isoformat() if row.date else None,
                        "count": row.count,
                    }
                    for row in daily_data
                ]

            # Monthly data (last year)
            monthly_cutoff = datetime.utcnow() - timedelta(days=365)
            monthly_data = (
                db.session.query(
                    func.date_trunc("month", Execution.start_date).label("month"),
                    func.count(Execution.id).label("count"),
                )
                .filter(Execution.start_date >= monthly_cutoff)
                .group_by(func.date_trunc("month", Execution.start_date))
                .order_by("month")
                .all()
            )

            trends["monthly_jobs"] = [
                {
                    "month": row.month.strftime("%Y-%m") if row.month else None,
                    "count": row.count,
                }
                for row in monthly_data
            ]

            return trends

        return StatsService._get_from_cache_or_execute(cache_key, execute_trends)

    @staticmethod
    def _get_geographic_data(period: str) -> dict[str, Any]:
        """Get geographic distribution of users."""
        cache_key = StatsService._get_cache_key("_get_geographic_data", period=period)

        def execute_geographic():
            cutoff_date = StatsService._get_time_filter(period)

            query = db.session.query(
                User.country, func.count(User.id).label("user_count")
            ).filter(User.country.isnot(None), User.country != "")

            if cutoff_date:
                query = query.filter(User.created_at >= cutoff_date)

            country_data = (
                query.group_by(User.country)
                .order_by(desc("user_count"))
                .limit(20)
                .all()
            )

            total_users = sum(row.user_count for row in country_data)

            top_countries = [
                {
                    "country": row.country,
                    "user_count": row.user_count,
                    "percentage": round((row.user_count / total_users * 100), 1)
                    if total_users > 0
                    else 0,
                }
                for row in country_data
            ]

            return {"top_countries": top_countries}

        return StatsService._get_from_cache_or_execute(cache_key, execute_geographic)

    @staticmethod
    def _get_task_stats(period: str) -> dict[str, Any]:
        """
        Get task execution statistics grouped by type and version.

        Args:
            period: Time period filter for data

        Returns:
            dict: Task statistics containing:
                - by_type: List of tasks with counts and success rates
                - by_version: List of script versions with usage counts and percentages
        """
        cutoff_date = StatsService._get_time_filter(period)

        # Task type statistics
        query = db.session.query(
            Script.slug,
            func.count(Execution.id).label("total_count"),
            func.count(func.nullif(Execution.status != "FINISHED", True)).label(
                "success_count"
            ),
        ).join(Execution, Execution.script_id == Script.id)

        if cutoff_date:
            query = query.filter(Execution.start_date >= cutoff_date)

        task_data = query.group_by(Script.slug).all()

        # Normalize task names and calculate success rates
        task_stats = {}
        for row in task_data:
            normalized_name = StatsService._normalize_task_name(row.slug)
            if normalized_name not in task_stats:
                task_stats[normalized_name] = {"count": 0, "success_count": 0}

            task_stats[normalized_name]["count"] += row.total_count
            task_stats[normalized_name]["success_count"] += row.success_count or 0

        by_type = [
            {
                "task": task_name,
                "count": stats["count"],
                "success_rate": round(
                    (stats["success_count"] / stats["count"] * 100), 1
                )
                if stats["count"] > 0
                else 0,
            }
            for task_name, stats in task_stats.items()
        ]
        by_type.sort(key=lambda x: x["count"], reverse=True)

        # Version statistics
        version_query = db.session.query(
            Script.slug, func.count(Execution.id).label("count")
        ).join(Execution, Execution.script_id == Script.id)

        if cutoff_date:
            version_query = version_query.filter(Execution.start_date >= cutoff_date)

        version_data = version_query.group_by(Script.slug).all()

        # Extract versions and calculate percentages
        version_stats = {}
        total_executions = sum(row.count for row in version_data)

        for row in version_data:
            version = StatsService._extract_version(row.slug)
            if version not in version_stats:
                version_stats[version] = 0
            version_stats[version] += row.count

        by_version = [
            {
                "version": version,
                "count": count,
                "percentage": round((count / total_executions * 100), 1)
                if total_executions > 0
                else 0,
            }
            for version, count in version_stats.items()
        ]
        by_version.sort(key=lambda x: x["count"], reverse=True)

        return {
            "by_type": by_type[:10],  # Top 10 tasks
            "by_version": by_version[:10],  # Top 10 versions
        }

    @staticmethod
    def _get_execution_time_series(
        period: str, group_by: str, task_type: str | None, status: str | None
    ) -> list[dict[str, Any]]:
        """Get execution time series data."""
        cutoff_date = StatsService._get_time_filter(period)

        # Determine truncation based on group_by
        trunc_format = {
            "hour": "hour",
            "day": "day",
            "week": "week",
            "month": "month",
        }.get(group_by, "day")

        query = db.session.query(
            func.date_trunc(trunc_format, Execution.start_date).label("timestamp"),
            func.count(Execution.id).label("total"),
            Execution.status,
            Script.slug,
        ).join(Script, Script.id == Execution.script_id)

        if cutoff_date:
            query = query.filter(Execution.start_date >= cutoff_date)

        if task_type:
            query = query.filter(Script.slug.like(f"%{task_type}%"))

        if status:
            query = query.filter(Execution.status == status)

        data = query.group_by(
            func.date_trunc(trunc_format, Execution.start_date),
            Execution.status,
            Script.slug,
        ).all()

        # Organize data by timestamp
        time_series = {}
        for row in data:
            timestamp = row.timestamp.isoformat() if row.timestamp else None
            if timestamp not in time_series:
                time_series[timestamp] = {
                    "timestamp": timestamp,
                    "total": 0,
                    "by_status": {},
                    "by_task": {},
                }

            time_series[timestamp]["total"] += 1

            # Count by status
            status_key = row.status or "UNKNOWN"
            time_series[timestamp]["by_status"][status_key] = (
                time_series[timestamp]["by_status"].get(status_key, 0) + 1
            )

            # Count by task (normalized)
            task_name = StatsService._normalize_task_name(row.slug)
            time_series[timestamp]["by_task"][task_name] = (
                time_series[timestamp]["by_task"].get(task_name, 0) + 1
            )

        return sorted(time_series.values(), key=lambda x: x["timestamp"] or "")

    @staticmethod
    def _get_top_users(period: str, task_type: str | None) -> list[dict[str, Any]]:
        """Get top users by execution count."""
        cutoff_date = StatsService._get_time_filter(period)

        query = (
            db.session.query(
                User.id,
                User.email,
                func.count(Execution.id).label("execution_count"),
                func.count(func.nullif(Execution.status != "FINISHED", True)).label(
                    "success_count"
                ),
            )
            .join(Execution, Execution.user_id == User.id)
            .join(Script, Script.id == Execution.script_id)
        )

        if cutoff_date:
            query = query.filter(Execution.start_date >= cutoff_date)

        if task_type:
            query = query.filter(Script.slug.like(f"%{task_type}%"))

        top_users_data = (
            query.group_by(User.id, User.email)
            .order_by(desc("execution_count"))
            .limit(10)
            .all()
        )

        result = []
        for row in top_users_data:
            success_rate = (
                (row.success_count / row.execution_count * 100)
                if row.execution_count > 0
                else 0
            )

            # Get favorite tasks for this user
            favorite_tasks = (
                db.session.query(Script.slug, func.count(Execution.id).label("count"))
                .join(Execution, Execution.script_id == Script.id)
                .filter(Execution.user_id == row.id)
                .group_by(Script.slug)
                .order_by(desc("count"))
                .limit(3)
                .all()
            )

            result.append(
                {
                    "user_id": str(row.id),  # Convert to string for consistency
                    "email": row.email,
                    "execution_count": row.execution_count,
                    "success_rate": round(success_rate, 1),
                    "favorite_tasks": [
                        StatsService._normalize_task_name(task.slug)
                        for task in favorite_tasks
                    ],
                }
            )

        return result

    @staticmethod
    def _get_task_performance(period: str) -> list[dict[str, Any]]:
        """Get task performance metrics."""
        cutoff_date = StatsService._get_time_filter(period)

        query = (
            db.session.query(
                Script.slug,
                func.count(Execution.id).label("total_executions"),
                func.count(func.nullif(Execution.status != "FINISHED", True)).label(
                    "success_count"
                ),
                func.avg(
                    func.extract("epoch", Execution.end_date - Execution.start_date)
                    / 60
                ).label("avg_duration_minutes"),
            )
            .join(Execution, Execution.script_id == Script.id)
            .filter(Execution.start_date.isnot(None), Execution.end_date.isnot(None))
        )

        if cutoff_date:
            query = query.filter(Execution.start_date >= cutoff_date)

        performance_data = query.group_by(Script.slug).all()

        result = []
        for row in performance_data:
            success_rate = (
                (row.success_count / row.total_executions * 100)
                if row.total_executions > 0
                else 0
            )

            result.append(
                {
                    "task": StatsService._normalize_task_name(row.slug),
                    "total_executions": row.total_executions,
                    "success_rate": round(success_rate, 1),
                    "avg_duration_minutes": round(row.avg_duration_minutes or 0, 1),
                    "failure_reasons": [],  # TODO: Extract from logs if needed
                }
            )

        return sorted(result, key=lambda x: x["total_executions"], reverse=True)

    @staticmethod
    def _get_registration_trends(
        period: str, group_by: str, country: str | None
    ) -> list[dict[str, Any]]:
        """Get user registration trends."""
        cutoff_date = StatsService._get_time_filter(period)

        trunc_format = {"day": "day", "week": "week", "month": "month"}.get(
            group_by, "day"
        )

        query = db.session.query(
            func.date_trunc(trunc_format, User.created_at).label("date"),
            func.count(User.id).label("new_users"),
        )

        if cutoff_date:
            query = query.filter(User.created_at >= cutoff_date)

        if country:
            query = query.filter(User.country == country)

        trends_data = (
            query.group_by(func.date_trunc(trunc_format, User.created_at))
            .order_by("date")
            .all()
        )

        # Calculate cumulative totals
        result = []
        total_users = 0
        for row in trends_data:
            total_users += row.new_users
            result.append(
                {
                    "date": row.date.date().isoformat() if row.date else None,
                    "new_users": row.new_users,
                    "total_users": total_users,
                }
            )

        return result

    @staticmethod
    def _get_activity_stats() -> dict[str, Any]:
        """Get user activity statistics."""
        now = datetime.utcnow()

        # Users who have executed jobs in different periods
        active_last_day = (
            db.session.query(func.count(func.distinct(Execution.user_id)))
            .filter(Execution.start_date >= now - timedelta(days=1))
            .scalar()
            or 0
        )

        active_last_week = (
            db.session.query(func.count(func.distinct(Execution.user_id)))
            .filter(Execution.start_date >= now - timedelta(days=7))
            .scalar()
            or 0
        )

        active_last_month = (
            db.session.query(func.count(func.distinct(Execution.user_id)))
            .filter(Execution.start_date >= now - timedelta(days=30))
            .scalar()
            or 0
        )

        # Users who have never executed a job
        total_users = db.session.query(func.count(User.id)).scalar() or 0
        users_with_executions = (
            db.session.query(func.count(func.distinct(Execution.user_id))).scalar() or 0
        )
        never_executed = total_users - users_with_executions

        return {
            "active_last_day": active_last_day,
            "active_last_week": active_last_week,
            "active_last_month": active_last_month,
            "never_executed": max(0, never_executed),
        }

    @staticmethod
    def _normalize_task_name(slug: str) -> str:
        """
        Normalize script slugs to consistent task names for grouping.

        Handles version removal, deprecated name mapping, and productivity variants
        to ensure consistent task categorization across statistics.

        Args:
            slug: Script slug from database (e.g., 'productivity-v2.1.0')

        Returns:
            str: Normalized task name (e.g., 'productivity') or 'unknown' for
                invalid input
        """
        if not slug:
            return "unknown"

        # Remove version suffixes
        task = slug.split("-v")[0]

        # Handle deprecated names
        task_mapping = {
            "sdg-sub-indicators": "sdg-15-3-1-sub-indicators",
            "vegetation-productivity": "productivity",
        }

        task = task_mapping.get(task, task)

        # Handle productivity variants
        if task.startswith("productivity-"):
            task = "productivity"

        return task

    # ============================================================================
    # CACHE MANAGEMENT METHODS
    # ============================================================================

    @staticmethod
    def clear_cache(pattern: str | None = None) -> bool:
        """
        Clear cached stats data.

        Args:
            pattern: Optional pattern to match cache keys (e.g., 'summary', 'trends')
                    If None, clears all stats cache

        Returns:
            bool: True if successful, False otherwise
        """
        redis_cache = get_redis_cache()

        if not redis_cache.is_available():
            logger.warning("Redis not available for cache clearing")
            return False

        try:
            if pattern:
                # Clear specific pattern
                cache_pattern = f"stats_service:*{pattern}*"
            else:
                # Clear all stats cache
                cache_pattern = "stats_service:*"
            # Get all matching keys
            if redis_cache.client:
                keys = list(redis_cache.client.scan_iter(match=cache_pattern))
                if keys:
                    deleted_count = redis_cache.client.delete(*keys)
                    logger.info(
                        f"Cleared {deleted_count} cache keys matching pattern: "
                        f"{cache_pattern}"
                    )
                    return True
                logger.info(f"No cache keys found matching pattern: {cache_pattern}")
                return True

        except Exception as e:
            logger.error(f"Failed to clear cache: {e}")
            return False

        return False

    @staticmethod
    def get_cache_info() -> dict[str, Any]:
        """
        Get information about cached stats data.

        Returns:
            Dictionary with cache information
        """
        redis_cache = get_redis_cache()

        if not redis_cache.is_available():
            return {"available": False, "error": "Redis not available"}

        try:
            cache_info = {"available": True, "keys": [], "total_keys": 0}

            if redis_cache.client:
                # Get all stats cache keys
                keys = list(redis_cache.client.scan_iter(match="stats_service:*"))
                cache_info["total_keys"] = len(keys)

                # Get details for each key
                for key in keys:
                    ttl = redis_cache.get_ttl(key)
                    cache_info["keys"].append(
                        {
                            "key": key,
                            "ttl_seconds": ttl,
                            "expires_in": f"{ttl // 60}m {ttl % 60}s"
                            if ttl > 0
                            else "expired"
                            if ttl == -1
                            else "no expiry",
                        }
                    )

                # Sort by TTL
                cache_info["keys"].sort(key=lambda x: x["ttl_seconds"], reverse=True)

            return cache_info

        except Exception as e:
            logger.error(f"Failed to get cache info: {e}")
            return {"available": False, "error": str(e)}

    @staticmethod
    def _extract_version(slug: str) -> str:
        """
        Extract major version number from script slug.

        Parses version patterns like '-v2.1.0' and returns the major version number.
        Handles both dot-separated (v2.1.0) and hyphen-separated (v2-1-0) formats.

        Args:
            slug: Script slug containing version (e.g., 'task-v2.1.0')

        Returns:
            str: Major version number (e.g., '2') or 'unknown' if no version found
        """
        if not slug:
            return "unknown"

        # Look for version pattern like -v2.1.0
        if "-v" in slug:
            version_part = slug.split("-v")[-1]
            # Convert hyphen-separated to dot-separated
            version = version_part.replace("-", ".")
            # Get major version (first part)
            return version.split(".")[0] if "." in version else version

        return "unknown"
