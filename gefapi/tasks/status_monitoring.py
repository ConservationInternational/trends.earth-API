"""STATUS MONITORING TASKS"""

import contextlib
import datetime
import logging

from celery import Task
import rollbar

from gefapi.services.docker_service import get_docker_client
from gefapi.utils.redis_cache import get_redis_cache

logger = logging.getLogger(__name__)

# Cache configuration
SWARM_CACHE_KEY = "docker_swarm_status"
SWARM_CACHE_TTL = 300  # 5 minutes TTL (buffer for 2-minute refresh cycle)
SWARM_CACHE_BACKUP_KEY = "docker_swarm_status_backup"  # Fallback cache key
SWARM_CACHE_BACKUP_TTL = 1800  # 30 minutes TTL for backup cache


class StatusMonitoringTask(Task):
    """Base task for status monitoring"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Status monitoring task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


def _get_docker_swarm_info():
    """
    Collect Docker Swarm node information including resource usage,
    container counts, leader status, and available capacity based on
    actual Docker Swarm resource reservations.

    This function queries the Docker Swarm API to get detailed information
    about each node in the swarm, including:
    - Node role (manager/worker) and leadership status
    - Current resource usage based on actual task reservations
    - Available capacity for additional tasks
    - Detailed resource metrics (CPU/memory usage percentages)

    Returns:
        dict: Docker swarm information with the following structure:
            {
                "swarm_active": bool,
                "total_nodes": int,
                "total_managers": int,
                "total_workers": int,
                "error": str | None,
                "nodes": [
                    {
                        "id": "node_id_string",
                        "hostname": "node-hostname",
                        "role": "manager" | "worker",
                        "is_manager": bool,
                        "is_leader": bool,
                        "availability": "active" | "pause" | "drain",
                        "state": "ready" | "down" | "unknown",
                        "cpu_count": float,  # Number of CPUs (e.g., 4.0)
                        "memory_gb": float,  # Memory in GB (e.g., 8.0)
                        "running_tasks": int,  # Number of running tasks
                        "available_capacity": int,  # Additional tasks that can fit
                        "resource_usage": {
                            "used_cpu_nanos": int,  # CPU nanoseconds used
                            "used_memory_bytes": int,  # Memory bytes used
                            "available_cpu_nanos": int,  # CPU nanoseconds available
                            "available_memory_bytes": int,  # Memory bytes available
                            "used_cpu_percent": float,  # CPU usage percentage
                            "used_memory_percent": float  # Memory usage percentage
                        },
                        "labels": dict,  # Node labels
                        "created_at": str,  # ISO timestamp
                        "updated_at": str   # ISO timestamp
                    }
                ]
            }

    Example Response:
        {
            "swarm_active": true,
            "total_nodes": 3,
            "total_managers": 1,
            "total_workers": 2,
            "error": null,
            "nodes": [
                {
                    "id": "abc123def456",
                    "hostname": "manager-node-1",
                    "role": "manager",
                    "is_manager": true,
                    "is_leader": true,
                    "availability": "active",
                    "state": "ready",
                    "cpu_count": 4.0,
                    "memory_gb": 8.0,
                    "running_tasks": 5,
                    "available_capacity": 35,
                    "resource_usage": {
                        "used_cpu_nanos": 500000000,
                        "used_memory_bytes": 524288000,
                        "available_cpu_nanos": 3500000000,
                        "available_memory_bytes": 7634903040,
                        "used_cpu_percent": 12.5,
                        "used_memory_percent": 6.25
                    },
                    "labels": {"node.role": "manager"},
                    "created_at": "2025-01-15T10:30:00Z",
                    "updated_at": "2025-01-15T12:45:00Z"
                }
            ]
        }
    """
    try:
        docker_client = get_docker_client()
        if docker_client is None:
            logger.warning("Docker client not available for swarm monitoring")
            return {
                "error": "Docker unavailable",
                "nodes": [],
                "total_nodes": 0,
                "total_managers": 0,
                "total_workers": 0,
                "swarm_active": False,
            }

        # Check if Docker is in swarm mode
        try:
            swarm_info = docker_client.info()
            if swarm_info.get("Swarm", {}).get("LocalNodeState") != "active":
                logger.info("Docker is not in swarm mode")
                return {
                    "error": "Not in swarm mode",
                    "nodes": [],
                    "total_nodes": 0,
                    "total_managers": 0,
                    "total_workers": 0,
                    "swarm_active": False,
                }
        except Exception as e:
            logger.error(f"Error checking swarm status: {e}")
            return {
                "error": f"Swarm check failed: {str(e)}",
                "nodes": [],
                "total_nodes": 0,
                "total_managers": 0,
                "total_workers": 0,
                "swarm_active": False,
            }

        # Get swarm nodes
        nodes = docker_client.nodes.list()
        node_details = []
        total_managers = 0
        total_workers = 0

        for node in nodes:
            try:
                node_attrs = node.attrs
                node_spec = node_attrs.get("Spec", {})
                node_status = node_attrs.get("Status", {})
                node_description = node_attrs.get("Description", {})
                node_resources = node_description.get("Resources", {})

                # Determine node role
                role = node_spec.get("Role", "worker")
                is_manager = role == "manager"
                is_leader = False

                if is_manager:
                    total_managers += 1
                    # Check if this manager is the leader
                    manager_status = node_attrs.get("ManagerStatus", {})
                    is_leader = manager_status.get("Leader", False)
                else:
                    total_workers += 1

                # Get node availability
                availability = node_spec.get("Availability", "unknown")

                # Get node state and status
                state = node_status.get("State", "unknown")

                # Get resource information
                nano_cpus = node_resources.get("NanoCPUs", 0)
                memory_bytes = node_resources.get("MemoryBytes", 0)

                # Convert nano CPUs to regular CPU count
                cpu_count = nano_cpus / 1_000_000_000 if nano_cpus else 0
                # Convert memory bytes to GB
                memory_gb = memory_bytes / (1024**3) if memory_bytes else 0

                # Get running tasks/containers on this node - this will be handled
                # in the capacity calculation section below
                tasks_on_node = 0

                # Calculate available capacity using Docker Swarm's actual resource
                # reservations. Docker Swarm tracks the exact resource reservations
                # for each running task
                used_cpu_nanos = 0
                used_memory_bytes = 0

                try:
                    # Get all services and their tasks running on this specific node
                    services = docker_client.services.list()
                    node_id = node_attrs.get("ID")

                    # Count tasks on this node and accumulate actual resource
                    # reservations
                    node_task_count = 0
                    for service in services:
                        service_tasks = service.tasks()
                        for task in service_tasks:
                            task_node_id = task.get("NodeID")
                            task_state = task.get("Status", {}).get("State", "")

                            if task_node_id == node_id and task_state in [
                                "running",
                                "starting",
                                "pending",
                            ]:
                                node_task_count += 1

                                # Get the actual resource reservations from the
                                # task spec
                                task_spec = task.get("Spec", {})
                                resources = task_spec.get("Resources", {})
                                reservations = resources.get("Reservations", {})

                                # Extract CPU reservations (in nanoseconds)
                                task_cpu_nanos = reservations.get("NanoCPUs", 0)
                                # Extract memory reservations (in bytes)
                                task_memory_bytes = reservations.get("MemoryBytes", 0)

                                # If no reservations are set, use service-level
                                # reservations
                                if task_cpu_nanos == 0 or task_memory_bytes == 0:
                                    try:
                                        service_spec = service.attrs.get("Spec", {})
                                        service_resources = service_spec.get(
                                            "TaskTemplate", {}
                                        ).get("Resources", {})
                                        service_reservations = service_resources.get(
                                            "Reservations", {}
                                        )

                                        if task_cpu_nanos == 0:
                                            task_cpu_nanos = service_reservations.get(
                                                "NanoCPUs", int(1e8)
                                            )  # Default 10% CPU
                                        if task_memory_bytes == 0:
                                            task_memory_bytes = (
                                                service_reservations.get(
                                                    "MemoryBytes", int(1e8)
                                                )
                                            )  # Default ~95MB
                                    except Exception as service_error:
                                        logger.debug(
                                            "Could not get service "
                                            f"reservations: {service_error}"
                                        )
                                        # Use defaults if we can't get service
                                        # reservations
                                        if task_cpu_nanos == 0:
                                            task_cpu_nanos = int(1e8)  # Default 10% CPU
                                        if task_memory_bytes == 0:
                                            task_memory_bytes = int(
                                                1e8
                                            )  # Default ~95MB

                                used_cpu_nanos += task_cpu_nanos
                                used_memory_bytes += task_memory_bytes

                    # Update tasks_on_node with our accurate count
                    tasks_on_node = node_task_count

                except Exception as resource_error:
                    logger.warning(
                        "Could not calculate Docker Swarm resource usage for "
                        f"node {node_attrs.get('ID')}: {resource_error}"
                    )
                    # Fallback to simple count-based estimation
                    used_cpu_nanos = tasks_on_node * int(
                        1e8
                    )  # Default 10% CPU per task
                    used_memory_bytes = tasks_on_node * int(
                        1e8
                    )  # Default ~95MB per task

                # Calculate remaining capacity
                node_cpu_nanos = nano_cpus
                node_memory_bytes = memory_bytes

                available_cpu_nanos = max(0, node_cpu_nanos - used_cpu_nanos)
                available_memory_bytes = max(0, node_memory_bytes - used_memory_bytes)

                # Calculate how many more tasks can fit based on default script
                # reservations
                default_cpu_reservation = int(1e8)  # 10% CPU
                default_memory_reservation = int(1e8)  # ~95MB

                max_additional_tasks_by_cpu = (
                    int(available_cpu_nanos / default_cpu_reservation)
                    if default_cpu_reservation > 0
                    else 0
                )
                max_additional_tasks_by_memory = (
                    int(available_memory_bytes / default_memory_reservation)
                    if default_memory_reservation > 0
                    else 0
                )

                # Available capacity is limited by the most constraining resource
                available_capacity = min(
                    max_additional_tasks_by_cpu, max_additional_tasks_by_memory
                )

                node_info = {
                    "id": node_attrs.get("ID"),
                    "hostname": node_description.get("Hostname", "unknown"),
                    "role": role,
                    "is_manager": is_manager,
                    "is_leader": is_leader,
                    "availability": availability,
                    "state": state,
                    "cpu_count": round(cpu_count, 2),
                    "memory_gb": round(memory_gb, 2),
                    "running_tasks": tasks_on_node,
                    "available_capacity": available_capacity,
                    "resource_usage": {
                        "used_cpu_nanos": used_cpu_nanos,
                        "used_memory_bytes": used_memory_bytes,
                        "available_cpu_nanos": available_cpu_nanos,
                        "available_memory_bytes": available_memory_bytes,
                        "used_cpu_percent": round(
                            (used_cpu_nanos / node_cpu_nanos * 100)
                            if node_cpu_nanos > 0
                            else 0,
                            2,
                        ),
                        "used_memory_percent": round(
                            (used_memory_bytes / node_memory_bytes * 100)
                            if node_memory_bytes > 0
                            else 0,
                            2,
                        ),
                    },
                    "labels": node_spec.get("Labels", {}),
                    "created_at": node_attrs.get("CreatedAt"),
                    "updated_at": node_attrs.get("UpdatedAt"),
                }

                node_details.append(node_info)

            except Exception as node_error:
                logger.error(f"Error processing node {node.id}: {node_error}")
                continue

        # Sort nodes by role (managers first) and then by hostname
        node_details.sort(key=lambda x: (not x["is_manager"], x["hostname"]))

        return {
            "nodes": node_details,
            "total_nodes": len(nodes),
            "total_managers": total_managers,
            "total_workers": total_workers,
            "swarm_active": True,
            "error": None,
        }

    except Exception as e:
        logger.error(f"Error collecting Docker swarm information: {e}")
        return {
            "error": str(e),
            "nodes": [],
            "total_nodes": 0,
            "total_managers": 0,
            "total_workers": 0,
            "swarm_active": False,
        }


def get_cached_swarm_status():
    """
    Get Docker Swarm status from cache with enhanced fallback handling.

    This function is safe to call from API services since it only reads from cache
    and never attempts Docker socket access. Docker swarm data is updated by
    periodic Celery tasks running on the build queue with Docker socket access.

    Implements a two-tier caching strategy:
    1. Primary cache (5 min TTL) - Fresh data updated every 2 minutes
    2. Backup cache (30 min TTL) - Fallback for reliability

    Returns:
        dict: Docker swarm information with node details and cache metadata.
              Always includes a 'cache_info' field with:
              - cached_at: ISO timestamp when data was cached
              - cache_ttl: Cache TTL in seconds (0 for unavailable data)
              - cache_key: Redis cache key used
            - source: Data source ('cached', 'backup_cache', 'legacy_cache',
              'cache_unavailable')
              - cache_hit: Boolean indicating if cache was hit
    """

    cache = get_redis_cache()
    cache_hit = False

    # Try to get from primary cache first
    if cache.is_available():
        cached_data = cache.get(SWARM_CACHE_KEY)
        if cached_data:
            logger.info("Retrieved Docker Swarm status from primary cache")
            cache_hit = True
            # Ensure cache_info exists and add performance metadata
            if "cache_info" not in cached_data:
                cached_data["cache_info"] = {
                    "cached_at": "unknown",
                    "cache_ttl": SWARM_CACHE_TTL,
                    "cache_key": SWARM_CACHE_KEY,
                    "source": "legacy_cache",
                }
            cached_data["cache_info"]["cache_hit"] = cache_hit
            return cached_data

        # Try backup cache if primary cache miss
        backup_data = cache.get(SWARM_CACHE_BACKUP_KEY)
        if backup_data:
            logger.info(
                "Retrieved Docker Swarm status from backup cache (primary cache miss)"
            )
            cache_hit = True
            # Update source and cache metadata
            if "cache_info" not in backup_data:
                backup_data["cache_info"] = {
                    "cached_at": "unknown",
                    "cache_ttl": SWARM_CACHE_BACKUP_TTL,
                    "cache_key": SWARM_CACHE_BACKUP_KEY,
                    "source": "backup_cache",
                }
            else:
                backup_data["cache_info"]["source"] = "backup_cache"
                backup_data["cache_info"]["cache_key"] = SWARM_CACHE_BACKUP_KEY
            backup_data["cache_info"]["cache_hit"] = cache_hit
            return backup_data

        logger.warning(
            "No cached Docker Swarm status found - both primary and backup cache empty"
        )
    else:
        logger.warning("Redis cache not available for Docker Swarm status")

    # Return unavailable status instead of attempting Docker access
    # This ensures API services never try to access Docker socket directly
    logger.info("Returning unavailable status - cache not accessible")
    return {
        "error": "Docker Swarm status unavailable - cache not accessible",
        "nodes": [],
        "total_nodes": 0,
        "total_managers": 0,
        "total_workers": 0,
        "swarm_active": False,
        "cache_info": {
            "cached_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "cache_ttl": 0,  # Not cached
            "cache_key": SWARM_CACHE_KEY,
            "source": "cache_unavailable",
            "cache_hit": False,
        },
    }


def update_swarm_cache():
    """
    Update the Docker Swarm status cache with fresh data and backup cache.

    Uses a two-tier caching strategy for improved reliability:
    - Primary cache: 5-minute TTL for fast response
    - Backup cache: 30-minute TTL for fallback when primary expires

    Returns:
        dict: The fresh swarm data that was cached, including cache metadata.
              The response includes a 'cache_info' field with:
              - cached_at: ISO timestamp when data was cached
              - cache_ttl: Cache TTL in seconds
              - cache_key: Redis cache key used
              - backup_cached: Whether backup cache was also updated
              - cache_operations: List of successful cache operations
    """

    cache = get_redis_cache()

    # Get fresh swarm data
    swarm_data = _get_docker_swarm_info()

    # Add cache metadata
    cache_timestamp = datetime.datetime.now(datetime.UTC)
    cache_operations = []

    swarm_data["cache_info"] = {
        "cached_at": cache_timestamp.isoformat(),
        "cache_ttl": SWARM_CACHE_TTL,
        "cache_key": SWARM_CACHE_KEY,
        "backup_cached": False,
        "cache_operations": cache_operations,
    }

    # Cache the data if Redis is available
    if cache.is_available():
        # Update primary cache
        primary_success = cache.set(SWARM_CACHE_KEY, swarm_data, SWARM_CACHE_TTL)
        if primary_success:
            logger.info(
                f"Successfully updated Docker Swarm primary cache at "
                f"{cache_timestamp.isoformat()}"
            )
            cache_operations.append("primary_cache_updated")
        else:
            logger.warning("Failed to update Docker Swarm primary cache")
            cache_operations.append("primary_cache_failed")

        # Update backup cache (only if we have valid data and no error)
        if not swarm_data.get("error"):
            backup_success = cache.set(
                SWARM_CACHE_BACKUP_KEY, swarm_data, SWARM_CACHE_BACKUP_TTL
            )
            if backup_success:
                logger.debug("Successfully updated Docker Swarm backup cache")
                swarm_data["cache_info"]["backup_cached"] = True
                cache_operations.append("backup_cache_updated")
            else:
                logger.warning("Failed to update Docker Swarm backup cache")
                cache_operations.append("backup_cache_failed")
        else:
            logger.debug("Skipping backup cache update due to error in swarm data")
            cache_operations.append("backup_cache_skipped_error")

    else:
        logger.warning("Redis cache not available, cannot cache swarm status")
        cache_operations.append("cache_unavailable")

    return swarm_data


def get_swarm_cache_statistics():
    """
    Get comprehensive cache statistics for monitoring and debugging.

    Returns cache hit rates, TTL information, and operational metrics
    for both primary and backup swarm status caches.

    Returns:
        dict: Cache statistics including:
              - cache_status: Overall cache health status
              - primary_cache: Primary cache information
              - backup_cache: Backup cache information
              - recommendations: Performance optimization suggestions
    """
    cache = get_redis_cache()

    stats = {
        "cache_status": "unavailable",
        "primary_cache": {
            "key": SWARM_CACHE_KEY,
            "exists": False,
            "ttl_seconds": -1,
            "data_available": False,
        },
        "backup_cache": {
            "key": SWARM_CACHE_BACKUP_KEY,
            "exists": False,
            "ttl_seconds": -1,
            "data_available": False,
        },
        "recommendations": [],
    }

    if not cache.is_available():
        stats["recommendations"].append(
            "Redis cache is not available - check connectivity"
        )
        return stats

    stats["cache_status"] = "available"

    # Check primary cache
    try:
        stats["primary_cache"]["exists"] = cache.exists(SWARM_CACHE_KEY)
        stats["primary_cache"]["ttl_seconds"] = cache.get_ttl(SWARM_CACHE_KEY)

        primary_data = cache.get(SWARM_CACHE_KEY)
        if primary_data:
            stats["primary_cache"]["data_available"] = True
            # Check data freshness
            cache_info = primary_data.get("cache_info", {})
            cached_at = cache_info.get("cached_at")
            if cached_at:
                try:
                    cached_time = datetime.datetime.fromisoformat(
                        cached_at.replace("Z", "+00:00")
                    )
                    age_seconds = (
                        datetime.datetime.now(datetime.UTC) - cached_time
                    ).total_seconds()
                    stats["primary_cache"]["age_seconds"] = round(age_seconds, 1)

                    if age_seconds > 300:  # Older than 5 minutes
                        stats["recommendations"].append(
                            "Primary cache data is stale - check refresh task"
                        )
                except Exception:
                    stats["recommendations"].append(
                        "Primary cache timestamp is invalid"
                    )
    except Exception as e:
        stats["recommendations"].append(f"Error checking primary cache: {str(e)}")

    # Check backup cache
    try:
        stats["backup_cache"]["exists"] = cache.exists(SWARM_CACHE_BACKUP_KEY)
        stats["backup_cache"]["ttl_seconds"] = cache.get_ttl(SWARM_CACHE_BACKUP_KEY)

        backup_data = cache.get(SWARM_CACHE_BACKUP_KEY)
        if backup_data:
            stats["backup_cache"]["data_available"] = True
            # Check backup data age
            cache_info = backup_data.get("cache_info", {})
            cached_at = cache_info.get("cached_at")
            if cached_at:
                try:
                    cached_time = datetime.datetime.fromisoformat(
                        cached_at.replace("Z", "+00:00")
                    )
                    age_seconds = (
                        datetime.datetime.now(datetime.UTC) - cached_time
                    ).total_seconds()
                    stats["backup_cache"]["age_seconds"] = round(age_seconds, 1)

                    if age_seconds > 1800:  # Older than 30 minutes
                        stats["recommendations"].append(
                            "Backup cache data is very stale"
                        )
                except Exception:
                    stats["recommendations"].append("Backup cache timestamp is invalid")
    except Exception as e:
        stats["recommendations"].append(f"Error checking backup cache: {str(e)}")

    # Generate recommendations
    if (
        not stats["primary_cache"]["data_available"]
        and not stats["backup_cache"]["data_available"]
    ):
        stats["recommendations"].append(
            "No cached data available - check Docker connectivity and refresh task"
        )
    elif (
        not stats["primary_cache"]["data_available"]
        and stats["backup_cache"]["data_available"]
    ):
        stats["recommendations"].append(
            "Primary cache empty, falling back to backup - check refresh task frequency"
        )

    if stats["primary_cache"]["ttl_seconds"] == -1 and stats["primary_cache"]["exists"]:
        stats["recommendations"].append(
            "Primary cache has no TTL - may persist indefinitely"
        )

    if not stats["recommendations"]:
        stats["recommendations"].append("Cache is healthy and operating normally")

    return stats


@celery.task(base=StatusMonitoringTask, bind=True)
def refresh_swarm_cache_task(self):
    """
    Periodic task to refresh Docker Swarm status cache with enhanced monitoring.
    This task should run every 2 minutes on the build queue.

    Implements performance monitoring and cache warming strategies:
    - Tracks cache update success/failure rates
    - Monitors Docker API response times
    - Ensures backup cache availability for reliability

    Returns:
        dict: Docker swarm data with cache metadata, including:
              - Standard swarm information (nodes, managers, workers, etc.)
              - cache_info with cached_at timestamp and other metadata
              - performance_metrics with timing and success information
              - On error: error details with cache_info indicating source as
                'refresh_task_error'
    """
    import datetime
    import time

    start_time = time.time()
    logger.info("[TASK]: Starting periodic Docker Swarm cache refresh")

    try:
        swarm_data = update_swarm_cache()
        cache_info = swarm_data.get("cache_info", {})

        # Add performance metrics
        refresh_duration = round(time.time() - start_time, 3)
        cache_operations = cache_info.get("cache_operations", [])

        swarm_data["performance_metrics"] = {
            "refresh_duration_seconds": refresh_duration,
            "refresh_timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "cache_operations_count": len(cache_operations),
            "cache_operations": cache_operations,
            "backup_cache_available": cache_info.get("backup_cached", False),
        }

        logger.info(
            f"[TASK]: Docker Swarm cache refreshed in {refresh_duration}s - "
            f"Active: {swarm_data['swarm_active']}, "
            f"Nodes: {swarm_data['total_nodes']}, "
            f"Managers: {swarm_data['total_managers']}, "
            f"Workers: {swarm_data['total_workers']}, "
            f"Cached at: {cache_info.get('cached_at', 'unknown')}, "
            f"Operations: {cache_operations}"
        )
        return swarm_data
    except Exception as error:
        refresh_duration = round(time.time() - start_time, 3)
        logger.error(
            f"[TASK]: Error refreshing Docker Swarm cache after "
            f"{refresh_duration}s: {str(error)}"
        )
        logger.exception("Full traceback:")

        # Report to rollbar if available
        with contextlib.suppress(Exception):
            rollbar.report_exc_info()

        # Return error info instead of raising to avoid task failure
        return {
            "error": f"Cache refresh failed: {str(error)}",
            "nodes": [],
            "total_nodes": 0,
            "total_managers": 0,
            "total_workers": 0,
            "swarm_active": False,
            "cache_info": {
                "cached_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "cache_ttl": 0,
                "cache_key": SWARM_CACHE_KEY,
                "source": "refresh_task_error",
                "cache_hit": False,
            },
            "performance_metrics": {
                "refresh_duration_seconds": refresh_duration,
                "refresh_timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                "cache_operations_count": 0,
                "cache_operations": ["task_error"],
                "backup_cache_available": False,
                "error_occurred": True,
            },
        }
