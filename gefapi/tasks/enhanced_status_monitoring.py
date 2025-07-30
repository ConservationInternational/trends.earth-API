"""ENHANCED STATUS MONITORING TASKS WITH DOCKER SWARM INFORMATION"""

import contextlib
import logging

from celery import Task
import rollbar
from sqlalchemy import func

from gefapi import db
from gefapi.models import Execution, Script, StatusLog, User
from gefapi.services.docker_service import get_docker_client

logger = logging.getLogger(__name__)


class EnhancedStatusMonitoringTask(Task):
    """Base task for enhanced status monitoring with Docker Swarm info"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Enhanced status monitoring task failed: {exc}")
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


@celery.task(base=EnhancedStatusMonitoringTask, bind=True)
def collect_enhanced_system_status(self):
    """
    Collect enhanced system status including detailed Docker Swarm node information.

    This task extends the original status collection with comprehensive swarm metrics
    that include actual resource usage based on Docker Swarm's task reservations
    rather than simple estimates.

    The task collects:
    - Standard system metrics (executions, users, scripts, CPU/memory)
    - Detailed Docker Swarm node information with actual resource usage
    - Available capacity calculations based on real resource reservations
    - Per-node resource utilization percentages

    Returns:
        dict: Enhanced status information with the following structure:
            {
                # Standard status fields
                "id": int,
                "timestamp": str,  # ISO timestamp
                "executions_active": int,
                "executions_ready": int,
                "executions_running": int,
                "executions_finished": int,
                "executions_failed": int,
                "executions_count": int,
                "users_count": int,
                "scripts_count": int,

                # Enhanced Docker Swarm information
                "docker_swarm": {
                    "swarm_active": bool,
                    "total_nodes": int,
                    "total_managers": int,
                    "total_workers": int,
                    "error": str | None,
                    "nodes": [
                        {
                            "id": str,
                            "hostname": str,
                            "role": "manager" | "worker",
                            "is_manager": bool,
                            "is_leader": bool,
                            "availability": str,
                            "state": str,
                            "cpu_count": float,
                            "memory_gb": float,
                            "running_tasks": int,
                            "available_capacity": int,
                            "resource_usage": {
                                "used_cpu_nanos": int,
                                "used_memory_bytes": int,
                                "available_cpu_nanos": int,
                                "available_memory_bytes": int,
                                "used_cpu_percent": float,
                                "used_memory_percent": float
                            },
                            "labels": dict,
                            "created_at": str,
                            "updated_at": str
                        }
                    ]
                }
            }

    Example Response:
        {
            "id": 123,
            "timestamp": "2025-01-15T14:30:00Z",
            "executions_active": 15,
            "executions_ready": 5,
            "executions_running": 10,
            "executions_finished": 245,
            "executions_failed": 12,
            "executions_count": 272,
            "users_count": 42,
            "scripts_count": 18,
            "docker_swarm": {
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
        }

    Raises:
        Exception: If status collection fails, the error is logged and re-raised
                  for Celery to handle according to retry policies.
    """
    logger.info(
        "[TASK]: Starting enhanced system status collection with Docker Swarm info"
    )

    # Import here to get the app instance
    from gefapi import app

    with app.app_context():
        try:
            # First, collect all the original status information
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

            # Get Docker Swarm information
            logger.info("[TASK]: Collecting Docker Swarm node information")
            swarm_info = _get_docker_swarm_info()
            logger.info(
                f"[TASK]: Docker Swarm - Active: {swarm_info['swarm_active']}, "
                f"Nodes: {swarm_info['total_nodes']}, "
                f"Managers: {swarm_info['total_managers']}, "
                f"Workers: {swarm_info['total_workers']}"
            )

            # Create status log entry (original fields only)
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

            # Return enhanced data including swarm information
            result = status_log.serialize()
            result["docker_swarm"] = swarm_info

            logger.info("[TASK]: Enhanced task completed successfully")
            return result

        except Exception as error:
            logger.error(
                f"[TASK]: Error collecting enhanced system status: {str(error)}"
            )
            logger.exception("Full traceback:")
            # Try to rollback the session
            with contextlib.suppress(Exception):
                db.session.rollback()

            # Report to rollbar if available
            with contextlib.suppress(Exception):
                rollbar.report_exc_info()

            # Re-raise the error so Celery can handle it
            raise error
