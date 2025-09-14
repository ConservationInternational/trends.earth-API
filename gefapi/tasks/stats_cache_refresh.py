"""
STATS CACHE REFRESH TASKS

Periodic tasks to refresh dashboard statistics cache proactively.
This improves performance by ensuring cached data is always warm
and reduces database load for frequently accessed endpoints.
"""

import contextlib
import logging

from celery import Task
import rollbar

from gefapi.services.stats_service import StatsService

logger = logging.getLogger(__name__)


class StatsCacheRefreshTask(Task):
    """Base task for stats cache refresh operations"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Stats cache refresh task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


@celery.task(base=StatsCacheRefreshTask, bind=True)
def refresh_dashboard_stats_cache(self):
    """
    Periodic task to refresh dashboard statistics cache.

    This task pre-calculates and caches the most commonly requested
    dashboard statistics to improve response times. It refreshes multiple
    cache entries for different parameter combinations.

    Runs every 4 minutes to ensure cache is always warm (cache TTL is 5 minutes).

    Returns:
        dict: Summary of cache refresh operations with counts and status
    """
    logger.info("[TASK]: Starting dashboard stats cache refresh")

    refresh_summary = {
        "total_refreshed": 0,
        "successful": 0,
        "failed": 0,
        "cache_keys": []
    }

    # Common parameter combinations to pre-cache
    refresh_configs = [
        # Most common dashboard requests
        {"period": "all", "include": ["summary", "trends", "geographic", "tasks"]},
        {"period": "last_month", "include": ["summary", "trends"]},
        {"period": "last_week", "include": ["summary"]},
        {"period": "last_year", "include": ["trends", "geographic"]},

        # Individual sections that might be requested separately
        {"period": "all", "include": ["summary"]},
        {"period": "all", "include": ["trends"]},
        {"period": "all", "include": ["geographic"]},
        {"period": "all", "include": ["tasks"]},
    ]

    for config in refresh_configs:
        try:
            logger.debug(f"Refreshing cache for config: {config}")

            # Call the service method to trigger cache refresh
            StatsService.get_dashboard_stats(
                period=config["period"], include=config["include"]
            )

            # Generate cache key for tracking
            cache_key = StatsService._get_cache_key(
                "get_dashboard_stats",
                period=config["period"],
                include=",".join(sorted(config["include"]))
            )

            refresh_summary["cache_keys"].append(cache_key)
            refresh_summary["successful"] += 1
            refresh_summary["total_refreshed"] += 1

            logger.debug(f"Successfully refreshed cache: {cache_key}")

        except Exception as e:
            logger.error(f"Failed to refresh cache for config {config}: {e}")
            refresh_summary["failed"] += 1
            refresh_summary["total_refreshed"] += 1

            # Report to rollbar but don't fail the entire task
            with contextlib.suppress(Exception):
                rollbar.report_exc_info()

    logger.info(
        f"[TASK]: Dashboard stats cache refresh completed - "
        f"Total: {refresh_summary['total_refreshed']}, "
        f"Successful: {refresh_summary['successful']}, "
        f"Failed: {refresh_summary['failed']}"
    )

    return refresh_summary


@celery.task(base=StatsCacheRefreshTask, bind=True)
def refresh_execution_stats_cache(self):
    """
    Periodic task to refresh execution statistics cache.

    Pre-caches commonly requested execution statistics to improve
    performance of the /stats/executions endpoint.

    Returns:
        dict: Summary of execution stats cache refresh operations
    """
    logger.info("[TASK]: Starting execution stats cache refresh")

    refresh_summary = {
        "total_refreshed": 0,
        "successful": 0,
        "failed": 0,
        "cache_keys": []
    }

    # Common execution stats parameter combinations
    execution_configs = [
        {"period": "last_month", "group_by": "day", "task_type": None, "status": None},
        {"period": "last_week", "group_by": "day", "task_type": None, "status": None},
        {"period": "last_year", "group_by": "month", "task_type": None, "status": None},
        {
            "period": "last_month",
            "group_by": "day",
            "task_type": None,
            "status": "FAILED",
        },
    ]

    for config in execution_configs:
        try:
            logger.debug(f"Refreshing execution stats cache for config: {config}")

            # Call the service method to trigger cache refresh
            StatsService.get_execution_stats(
                period=config["period"],
                group_by=config["group_by"],
                task_type=config["task_type"],
                status=config["status"],
            )

            # Generate cache key for tracking
            cache_key = StatsService._get_cache_key(
                "get_execution_stats",
                period=config["period"],
                group_by=config["group_by"],
                task_type=config["task_type"] or "none",
                status=config["status"] or "none"
            )

            refresh_summary["cache_keys"].append(cache_key)
            refresh_summary["successful"] += 1
            refresh_summary["total_refreshed"] += 1

            logger.debug(f"Successfully refreshed execution stats cache: {cache_key}")

        except Exception as e:
            logger.error(
                f"Failed to refresh execution stats cache for config {config}: {e}"
            )
            refresh_summary["failed"] += 1
            refresh_summary["total_refreshed"] += 1

            # Report to rollbar but don't fail the entire task
            with contextlib.suppress(Exception):
                rollbar.report_exc_info()

    logger.info(
        f"[TASK]: Execution stats cache refresh completed - "
        f"Total: {refresh_summary['total_refreshed']}, "
        f"Successful: {refresh_summary['successful']}, "
        f"Failed: {refresh_summary['failed']}"
    )

    return refresh_summary


@celery.task(base=StatsCacheRefreshTask, bind=True)
def refresh_user_stats_cache(self):
    """
    Periodic task to refresh user statistics cache.

    Pre-caches commonly requested user statistics to improve
    performance of the /stats/users endpoint.

    Returns:
        dict: Summary of user stats cache refresh operations
    """
    logger.info("[TASK]: Starting user stats cache refresh")

    refresh_summary = {
        "total_refreshed": 0,
        "successful": 0,
        "failed": 0,
        "cache_keys": []
    }

    # Common user stats parameter combinations
    user_configs = [
        {"period": "last_year", "group_by": "month", "country": None},
        {"period": "last_month", "group_by": "day", "country": None},
        {"period": "all", "group_by": "month", "country": None},
    ]

    for config in user_configs:
        try:
            logger.debug(f"Refreshing user stats cache for config: {config}")

            # Call the service method to trigger cache refresh
            StatsService.get_user_stats(
                period=config["period"],
                group_by=config["group_by"],
                country=config["country"],
            )

            # Generate cache key for tracking
            cache_key = StatsService._get_cache_key(
                "get_user_stats",
                period=config["period"],
                group_by=config["group_by"],
                country=config["country"] or "none"
            )

            refresh_summary["cache_keys"].append(cache_key)
            refresh_summary["successful"] += 1
            refresh_summary["total_refreshed"] += 1

            logger.debug(f"Successfully refreshed user stats cache: {cache_key}")

        except Exception as e:
            logger.error(f"Failed to refresh user stats cache for config {config}: {e}")
            refresh_summary["failed"] += 1
            refresh_summary["total_refreshed"] += 1

            # Report to rollbar but don't fail the entire task
            with contextlib.suppress(Exception):
                rollbar.report_exc_info()

    logger.info(
        f"[TASK]: User stats cache refresh completed - "
        f"Total: {refresh_summary['total_refreshed']}, "
        f"Successful: {refresh_summary['successful']}, "
        f"Failed: {refresh_summary['failed']}"
    )

    return refresh_summary


@celery.task(base=StatsCacheRefreshTask, bind=True)
def warmup_stats_cache_on_startup(self):
    """
    One-time task to warm up the stats cache on application startup.

    This ensures that the most critical stats are cached immediately
    after system startup, providing good performance from the first request.

    Returns:
        dict: Summary of cache warmup operations
    """
    logger.info("[TASK]: Starting stats cache warmup on startup")

    warmup_summary = {
        "dashboard_refresh": None,
        "execution_refresh": None,
        "user_refresh": None,
        "total_operations": 0,
        "total_successful": 0,
        "total_failed": 0
    }

    try:
        # Refresh dashboard stats cache
        dashboard_result = refresh_dashboard_stats_cache.apply()
        warmup_summary["dashboard_refresh"] = dashboard_result.result
        warmup_summary["total_operations"] += dashboard_result.result.get(
            "total_refreshed", 0
        )
        warmup_summary["total_successful"] += dashboard_result.result.get(
            "successful", 0
        )
        warmup_summary["total_failed"] += dashboard_result.result.get("failed", 0)

    except Exception as e:
        logger.error(f"Failed to refresh dashboard stats during warmup: {e}")
        warmup_summary["dashboard_refresh"] = {"error": str(e)}

    try:
        # Refresh execution stats cache
        execution_result = refresh_execution_stats_cache.apply()
        warmup_summary["execution_refresh"] = execution_result.result
        warmup_summary["total_operations"] += execution_result.result.get(
            "total_refreshed", 0
        )
        warmup_summary["total_successful"] += execution_result.result.get(
            "successful", 0
        )
        warmup_summary["total_failed"] += execution_result.result.get("failed", 0)

    except Exception as e:
        logger.error(f"Failed to refresh execution stats during warmup: {e}")
        warmup_summary["execution_refresh"] = {"error": str(e)}

    try:
        # Refresh user stats cache
        user_result = refresh_user_stats_cache.apply()
        warmup_summary["user_refresh"] = user_result.result
        warmup_summary["total_operations"] += user_result.result.get(
            "total_refreshed", 0
        )
        warmup_summary["total_successful"] += user_result.result.get("successful", 0)
        warmup_summary["total_failed"] += user_result.result.get("failed", 0)

    except Exception as e:
        logger.error(f"Failed to refresh user stats during warmup: {e}")
        warmup_summary["user_refresh"] = {"error": str(e)}

    logger.info(
        f"[TASK]: Stats cache warmup completed - "
        f"Total operations: {warmup_summary['total_operations']}, "
        f"Successful: {warmup_summary['total_successful']}, "
        f"Failed: {warmup_summary['total_failed']}"
    )

    return warmup_summary
