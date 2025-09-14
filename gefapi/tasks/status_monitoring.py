"""STATUS MONITORING TASKS"""

from collections import defaultdict
import contextlib
import datetime
import logging
import time

from celery import Task
import rollbar

from gefapi.services.docker_service import get_docker_client
from gefapi.utils.redis_cache import get_redis_cache

logger = logging.getLogger(__name__)

# Cache configuration
SWARM_CACHE_KEY = "docker_swarm_status"
SWARM_CACHE_TTL = 300  # 5 minutes TTL (buffer for 2-minute refresh cycle)

# Performance monitoring cache key
SWARM_PERF_CACHE_KEY = "docker_swarm_performance"

# Optimization: Cache task data to avoid repeated API calls
_TASK_DATA_CACHE = {}
_TASK_CACHE_EXPIRY = 0


class StatusMonitoringTask(Task):
    """Base task for status monitoring"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Status monitoring task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


def _get_optimized_task_data(docker_client):
    """
    Optimized collection of task data with caching to avoid repeated API calls.

    This function collects all task data in a single pass and groups it by node,
    significantly reducing the number of Docker API calls compared to the
    per-node approach.

    Returns:
        dict: Task data grouped by node_id with resource usage information
    """
    current_time = time.time()

    # Use cached data if still valid (30 second cache for task data)
    global _TASK_DATA_CACHE, _TASK_CACHE_EXPIRY
    if current_time < _TASK_CACHE_EXPIRY and _TASK_DATA_CACHE:
        logger.debug("Using cached task data for resource calculations")
        return _TASK_DATA_CACHE

    logger.debug("Collecting fresh task data for resource calculations")

    # Initialize result structure
    tasks_by_node = defaultdict(lambda: {
        'task_count': 0,
        'used_cpu_nanos': 0,
        'used_memory_bytes': 0
    })

    try:
        start_time = time.time()

        # Get all services once - major optimization
        services = docker_client.services.list()

        # Process all tasks in a single loop
        for service in services:
            try:
                service_tasks = service.tasks()

                # Pre-fetch service-level reservations to avoid repeated lookups
                service_spec = service.attrs.get("Spec", {})
                service_resources = service_spec.get("TaskTemplate", {}).get(
                    "Resources", {}
                )
                service_reservations = service_resources.get("Reservations", {})
                # Default 10% CPU
                service_cpu_nanos = service_reservations.get(
                    "NanoCPUs", int(1e8)
                )
                # Default ~95MB
                service_memory_bytes = service_reservations.get(
                    "MemoryBytes", int(1e8)
                )

                for task in service_tasks:
                    task_node_id = task.get("NodeID")
                    task_state = task.get("Status", {}).get("State", "")

                    # Only count running/active tasks
                    if (task_state in ["running", "starting", "pending"]
                            and task_node_id):
                        node_data = tasks_by_node[task_node_id]
                        node_data['task_count'] += 1

                        # Get task-specific reservations or fall back to service
                        # defaults
                        task_spec = task.get("Spec", {})
                        resources = task_spec.get("Resources", {})
                        reservations = resources.get("Reservations", {})

                        task_cpu_nanos = reservations.get(
                            "NanoCPUs", service_cpu_nanos
                        )
                        task_memory_bytes = reservations.get(
                            "MemoryBytes", service_memory_bytes
                        )

                        node_data['used_cpu_nanos'] += task_cpu_nanos
                        node_data['used_memory_bytes'] += task_memory_bytes

            except Exception as service_error:
                logger.debug(f"Error processing service {service.id}: {service_error}")
                continue

        # Cache the results for 30 seconds
        _TASK_DATA_CACHE = dict(tasks_by_node)
        _TASK_CACHE_EXPIRY = current_time + 30

        collection_time = time.time() - start_time
        logger.debug(f"Task data collection completed in {collection_time:.2f} seconds")

        # Store collection time for performance tracking
        _get_optimized_task_data._last_collection_time = collection_time

        return _TASK_DATA_CACHE

    except Exception as e:
        logger.warning(f"Error in optimized task data collection: {e}")
        # Return empty data on error
        return {}


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

    OPTIMIZATIONS IMPLEMENTED:
    - Pre-collection of all task data to avoid N+1 API calls
    - Caching of task data for 30 seconds to avoid repeated calculations
    - Performance monitoring and timing collection
    - Efficient data structure grouping by node_id

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
                ],
                "_performance": {  # Performance monitoring metadata
                    "collection_time_seconds": float,
                    "node_count": int,
                    "task_collection_time_seconds": float,
                    "cache_used": bool
                }
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
            ],
            "_performance": {
                "collection_time_seconds": 0.85,
                "node_count": 3,
                "task_collection_time_seconds": 0.42,
                "cache_used": false
            }
        }
    """
    collection_start_time = time.time()

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

        # OPTIMIZATION: Collect all task data once instead of per-node
        logger.debug("Collecting task data for resource calculations")
        tasks_by_node = _get_optimized_task_data(docker_client)

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

                # OPTIMIZATION: Use pre-collected task data instead of nested loops
                node_id = node_attrs.get("ID")
                node_task_data = tasks_by_node.get(node_id, {
                    'task_count': 0,
                    'used_cpu_nanos': 0,
                    'used_memory_bytes': 0
                })

                tasks_on_node = node_task_data['task_count']
                used_cpu_nanos = node_task_data['used_cpu_nanos']
                used_memory_bytes = node_task_data['used_memory_bytes']

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

        # Calculate performance metrics
        collection_time = time.time() - collection_start_time
        task_collection_time = getattr(
            _get_optimized_task_data, '_last_collection_time', 0
        )
        cache_used = collection_time < task_collection_time  # Approximation

        result = {
            "nodes": node_details,
            "total_nodes": len(nodes),
            "total_managers": total_managers,
            "total_workers": total_workers,
            "swarm_active": True,
            "error": None,
            "_performance": {
                "collection_time_seconds": round(collection_time, 3),
                "node_count": len(nodes),
                "task_collection_time_seconds": round(task_collection_time, 3),
                "cache_used": cache_used
            }
        }

        # Store performance data in cache for monitoring
        _store_performance_metrics(result["_performance"])

        return result

    except Exception as e:
        collection_time = time.time() - collection_start_time
        logger.error(f"Error collecting Docker swarm information: {e}")
        return {
            "error": str(e),
            "nodes": [],
            "total_nodes": 0,
            "total_managers": 0,
            "total_workers": 0,
            "swarm_active": False,
            "_performance": {
                "collection_time_seconds": round(collection_time, 3),
                "node_count": 0,
                "task_collection_time_seconds": 0,
                "cache_used": False,
                "error": True
            }
        }


def _store_performance_metrics(performance_data):
    """Store performance metrics in Redis for monitoring."""
    try:
        cache = get_redis_cache()
        if cache.is_available():
            # Add timestamp to performance data
            performance_data["timestamp"] = datetime.datetime.now(
                datetime.UTC
            ).isoformat()

            # Store with short TTL for monitoring purposes
            cache.set(SWARM_PERF_CACHE_KEY, performance_data, 600)  # 10 minutes

            # Log performance if it's notably slow
            if performance_data.get("collection_time_seconds", 0) > 2.0:
                logger.warning(
                    f"Slow swarm data collection detected: "
                    f"{performance_data['collection_time_seconds']:.2f}s for "
                    f"{performance_data['node_count']} nodes"
                )
    except Exception as e:
        logger.debug(f"Could not store performance metrics: {e}")


def get_cached_swarm_status():
    """
    Get Docker Swarm status from cache only - never accesses Docker directly.

    This function is safe to call from API services since it only reads from cache
    and never attempts Docker socket access. Docker swarm data is updated by
    periodic Celery tasks running on the build queue with Docker socket access.

    Returns:
        dict: Docker swarm information with node details and cache metadata.
              Always includes a 'cache_info' field with:
              - cached_at: ISO timestamp when data was cached
              - cache_ttl: Cache TTL in seconds (0 for unavailable data)
              - cache_key: Redis cache key used
              - source: Data source ('cached', 'legacy_cache', 'cache_unavailable')
    """

    cache = get_redis_cache()

    # Try to get from cache first
    if cache.is_available():
        cached_data = cache.get(SWARM_CACHE_KEY)
        if cached_data:
            logger.info("Retrieved Docker Swarm status from cache")
            # Ensure cache_info exists (for backward compatibility)
            if "cache_info" not in cached_data:
                cached_data["cache_info"] = {
                    "cached_at": "unknown",
                    "cache_ttl": SWARM_CACHE_TTL,
                    "cache_key": SWARM_CACHE_KEY,
                    "source": "legacy_cache",
                }
            return cached_data
        logger.warning("No cached Docker Swarm status found - cache empty")
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
        },
    }


def update_swarm_cache():
    """
    Update the Docker Swarm status cache with fresh data.

    Returns:
        dict: The fresh swarm data that was cached, including cache metadata.
              The response includes a 'cache_info' field with:
              - cached_at: ISO timestamp when data was cached
              - cache_ttl: Cache TTL in seconds
              - cache_key: Redis cache key used
    """

    cache = get_redis_cache()

    # Get fresh swarm data
    swarm_data = _get_docker_swarm_info()

    # Add cache metadata
    cache_timestamp = datetime.datetime.now(datetime.UTC)
    swarm_data["cache_info"] = {
        "cached_at": cache_timestamp.isoformat(),
        "cache_ttl": SWARM_CACHE_TTL,
        "cache_key": SWARM_CACHE_KEY,
    }

    # Cache the data if Redis is available
    if cache.is_available():
        success = cache.set(SWARM_CACHE_KEY, swarm_data, SWARM_CACHE_TTL)
        if success:
            logger.info(
                f"Successfully updated Docker Swarm status cache at "
                f"{cache_timestamp.isoformat()}"
            )
        else:
            logger.warning("Failed to update Docker Swarm status cache")
    else:
        logger.warning("Redis cache not available, cannot cache swarm status")

    return swarm_data



@celery.task(base=StatusMonitoringTask, bind=True)
def warm_swarm_cache_on_startup(self):
    """
    Warm the Docker Swarm cache on application startup.

    This task should be called once when the application starts to ensure
    that swarm data is immediately available without waiting for the first
    scheduled refresh.

    Returns:
        dict: Result of cache warming operation with metadata
    """
    logger.info("[STARTUP]: Warming Docker Swarm cache on application startup")

    try:
        # Force a fresh cache update
        swarm_data = update_swarm_cache()

        cache_info = swarm_data.get("cache_info", {})
        logger.info(
            f"[STARTUP]: Docker Swarm cache warmed successfully - "
            f"Active: {swarm_data['swarm_active']}, "
            f"Nodes: {swarm_data['total_nodes']}, "
            f"Cached at: {cache_info.get('cached_at', 'unknown')}"
        )

        return {
            "success": True,
            "swarm_data": swarm_data,
            "message": "Cache warmed successfully on startup"
        }

    except Exception as error:
        logger.error(f"[STARTUP]: Error warming Docker Swarm cache: {str(error)}")
        return {
            "success": False,
            "error": str(error),
            "message": "Cache warming failed on startup"
        }


@celery.task(base=StatusMonitoringTask, bind=True)
def refresh_swarm_cache_task(self):
    """
    Periodic task to refresh Docker Swarm status cache.
    This task should run every 2 minutes on the build queue.

    Returns:
        dict: Docker swarm data with cache metadata, including:
              - Standard swarm information (nodes, managers, workers, etc.)
              - cache_info with cached_at timestamp and other metadata
              - On error: error details with cache_info indicating source as
                'refresh_task_error'
    """
    logger.info("[TASK]: Starting periodic Docker Swarm cache refresh")

    try:
        swarm_data = update_swarm_cache()
        cache_info = swarm_data.get("cache_info", {})
        logger.info(
            f"[TASK]: Docker Swarm cache refreshed - "
            f"Active: {swarm_data['swarm_active']}, "
            f"Nodes: {swarm_data['total_nodes']}, "
            f"Managers: {swarm_data['total_managers']}, "
            f"Workers: {swarm_data['total_workers']}, "
            f"Cached at: {cache_info.get('cached_at', 'unknown')}"
        )
        return swarm_data
    except Exception as error:
        import datetime

        logger.error(f"[TASK]: Error refreshing Docker Swarm cache: {str(error)}")
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
            },
        }
