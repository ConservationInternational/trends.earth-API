"""STATUS MONITORING TASKS"""

import contextlib
import logging

from celery import Task
import rollbar
from sqlalchemy import func
from sqlalchemy.exc import DisconnectionError, OperationalError

from gefapi import db
from gefapi.models import Execution, Script, StatusLog, User
from gefapi.services.docker_service import get_docker_client
from gefapi.utils.database import retry_db_operation, test_database_connection
from gefapi.utils.redis_cache import get_redis_cache

logger = logging.getLogger(__name__)

# Cache configuration
SWARM_CACHE_KEY = "docker_swarm_status"
SWARM_CACHE_TTL = 300  # 5 minutes TTL (buffer for 2-minute refresh cycle)


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
    container counts, leader status, and available capacity.

    Returns:
        dict: Docker swarm information with node details
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

                # Get running tasks/containers on this node
                try:
                    # Get all services and their tasks
                    services = docker_client.services.list()
                    tasks_on_node = 0

                    for service in services:
                        service_tasks = service.tasks()
                        for task in service_tasks:
                            task_node_id = task.get("NodeID")
                            task_state = task.get("Status", {}).get("State", "")
                            if task_node_id == node_attrs.get("ID") and task_state in [
                                "running",
                                "starting",
                                "pending",
                            ]:
                                tasks_on_node += 1

                except Exception as task_error:
                    logger.warning(
                        f"Could not get task count for node "
                        f"{node_attrs.get('ID')}: {task_error}"
                    )
                    tasks_on_node = 0

                # Calculate available capacity (simplified)
                # This is a rough estimate - in reality, capacity depends on
                # many factors
                max_tasks_estimate = (
                    int(cpu_count * 2) if cpu_count > 0 else 0
                )  # Rough estimate
                available_capacity = max(0, max_tasks_estimate - tasks_on_node)

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
                    "estimated_max_tasks": max_tasks_estimate,
                    "available_capacity": available_capacity,
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
    Get Docker Swarm status from cache, with fallback to enhanced real-time data.

    Returns:
        dict: Docker swarm information with enhanced node details and cache metadata.
              Always includes a 'cache_info' field with:
              - cached_at: ISO timestamp when data was cached (or retrieved)
              - cache_ttl: Cache TTL in seconds (0 for non-cached data)
              - cache_key: Redis cache key used
              - source: Data source ('cached', 'legacy_cache', 'real_time_fallback')
    """
    import datetime

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
        logger.info("No cached Docker Swarm status found")

    # Fallback to enhanced real-time data if cache miss or unavailable
    logger.info("Fetching enhanced Docker Swarm status as fallback")
    from gefapi.tasks.enhanced_status_monitoring import (
        _get_docker_swarm_info as _get_enhanced_docker_swarm_info,
    )

    swarm_data = _get_enhanced_docker_swarm_info()

    # Add cache info to indicate this is real-time data
    swarm_data["cache_info"] = {
        "cached_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "cache_ttl": 0,  # Not cached
        "cache_key": SWARM_CACHE_KEY,
        "source": "real_time_fallback",
    }

    return swarm_data


def update_swarm_cache():
    """
    Update the Docker Swarm status cache with fresh enhanced data.

    Returns:
        dict: The fresh enhanced swarm data that was cached, including cache metadata.
              The response includes a 'cache_info' field with:
              - cached_at: ISO timestamp when data was cached
              - cache_ttl: Cache TTL in seconds
              - cache_key: Redis cache key used
    """
    import datetime

    cache = get_redis_cache()

    # Get fresh enhanced swarm data
    from gefapi.tasks.enhanced_status_monitoring import (
        _get_docker_swarm_info as _get_enhanced_docker_swarm_info,
    )

    swarm_data = _get_enhanced_docker_swarm_info()

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


@celery.task(base=StatusMonitoringTask, bind=True)
@retry_db_operation(max_retries=3, backoff_seconds=2)
def collect_system_status(self):
    """
    Collect system status and save to status_log table.

    Returns:
        dict: System status information including:
              - Standard StatusLog fields (executions, users, scripts, etc.)
              - docker_swarm: Docker Swarm information with cache metadata
                - cache_info field indicating when swarm data was cached/retrieved
                - source field showing data origin (cached, real_time_fallback,
                  error_fallback)
    """
    logger.info("[TASK]: Starting system status collection")

    # Import here to get the app instance
    from gefapi import app

    with app.app_context():
        try:
            # Test database connectivity before proceeding
            logger.info("[TASK]: Testing database connectivity")
            if not test_database_connection():
                logger.warning(
                    "[TASK]: Initial database connection test failed, "
                    "disposing connection pool"
                )
                db.engine.dispose()
                # Test again after disposing
                if not test_database_connection():
                    raise OperationalError(
                        "Database connection unavailable", None, None
                    )

            # Ensure we have a fresh database session
            db.session.close()

            # Count executions by status
            logger.info("[TASK]: Querying execution counts")
            execution_counts = (
                db.session.query(Execution.status, func.count(Execution.id))
                .group_by(Execution.status)
                .all()
            )

            execution_status_map = dict(execution_counts)
            executions_active = execution_status_map.get(
                "RUNNING", 0
            ) + execution_status_map.get("PENDING", 0)
            executions_ready = execution_status_map.get("READY", 0)
            executions_running = execution_status_map.get("RUNNING", 0)

            # Count executions finished and failed since the last status log
            logger.info(
                "[TASK]: Querying executions finished and failed since last status log"
            )
            last_status_log = (
                db.session.query(StatusLog).order_by(StatusLog.timestamp.desc()).first()
            )

            if last_status_log:
                # Count executions that finished after the last status log timestamp
                executions_finished = (
                    db.session.query(func.count(Execution.id))
                    .filter(
                        Execution.status == "FINISHED",
                        Execution.end_date > last_status_log.timestamp,
                    )
                    .scalar()
                    or 0
                )

                # Count executions that failed after the last status log timestamp
                executions_failed = (
                    db.session.query(func.count(Execution.id))
                    .filter(
                        Execution.status == "FAILED",
                        Execution.end_date > last_status_log.timestamp,
                    )
                    .scalar()
                    or 0
                )

                logger.info(
                    f"[TASK]: Found {executions_finished} executions finished and "
                    f"{executions_failed} executions failed since last status log at "
                    f"{last_status_log.timestamp}"
                )
            else:
                # If no previous status log exists, count all finished and failed
                # executions
                executions_finished = execution_status_map.get("FINISHED", 0)
                executions_failed = execution_status_map.get("FAILED", 0)
                logger.info(
                    "[TASK]: No previous status log found, counting all finished "
                    "and failed executions"
                )

            logger.info(
                f"[TASK]: Execution counts - Active: {executions_active}, "
                f"Running: {executions_running}, Finished: {executions_finished}, "
                f"Failed: {executions_failed}"
            )

            # Count total executions
            logger.info("[TASK]: Querying total execution count")
            executions_count = db.session.query(func.count(Execution.id)).scalar() or 0

            logger.info(f"[TASK]: Total executions count: {executions_count}")

            # Count users and scripts
            logger.info("[TASK]: Querying user and script counts")
            users_count = db.session.query(func.count(User.id)).scalar() or 0
            scripts_count = db.session.query(func.count(Script.id)).scalar() or 0

            logger.info(
                f"[TASK]: Counts - Users: {users_count}, Scripts: {scripts_count}"
            )

            # System metrics tracking removed - no longer collecting CPU/memory data

            # Get Docker Swarm information from cache (fast)
            logger.info("[TASK]: Getting Docker Swarm information from cache")
            try:
                swarm_info = get_cached_swarm_status()
                cache_source = swarm_info.get("cache_info", {}).get("source", "unknown")
                logger.info(
                    f"[TASK]: Docker Swarm info retrieved - "
                    f"Active: {swarm_info['swarm_active']}, "
                    f"Nodes: {swarm_info['total_nodes']}, "
                    f"Managers: {swarm_info['total_managers']}, "
                    f"Workers: {swarm_info['total_workers']}, "
                    f"Cache source: {cache_source}"
                )
            except Exception as swarm_error:
                import datetime

                logger.warning(
                    f"[TASK]: Failed to get cached Docker Swarm info: {swarm_error}"
                )
                # Fallback to basic error response
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
                        "cache_key": SWARM_CACHE_KEY,
                        "source": "error_fallback",
                    },
                }

            # Create status log entry
            logger.info("[TASK]: Creating status log entry")
            status_log = StatusLog(
                executions_active=executions_active,
                executions_ready=executions_ready,
                executions_running=executions_running,
                executions_finished=executions_finished,
                executions_failed=executions_failed,
                executions_count=executions_count,
                users_count=users_count,
                scripts_count=scripts_count,
            )

            logger.info("[DB]: Adding status log to database")
            db.session.add(status_log)
            db.session.commit()

            logger.info(
                f"[TASK]: Status log created successfully with ID {status_log.id} "
                f"at {status_log.timestamp}"
            )
            # Return serialized data for task result with Docker Swarm info
            result = status_log.serialize()
            result["docker_swarm"] = swarm_info
            logger.info(f"[TASK]: Task completed successfully, returning: {result}")
            return result

        except Exception as error:
            logger.error(f"[TASK]: Error collecting system status: {str(error)}")
            logger.exception("Full traceback:")

            # Enhanced database cleanup for connection issues
            try:
                db.session.rollback()
                logger.info("[DB]: Session rollback completed")
            except Exception as rollback_error:
                logger.warning(f"[DB]: Error during session rollback: {rollback_error}")

            # If this is a database connection error, dispose of the connection pool
            if isinstance(error, (OperationalError, DisconnectionError)):
                try:
                    db.engine.dispose()
                    logger.info(
                        "[DB]: Connection pool disposed due to connection error"
                    )
                except Exception as dispose_error:
                    logger.warning(
                        f"[DB]: Error disposing connection pool: {dispose_error}"
                    )

            # Report to rollbar if available
            with contextlib.suppress(Exception):
                rollbar.report_exc_info()

            # Re-raise the error so Celery can handle it
            raise error
