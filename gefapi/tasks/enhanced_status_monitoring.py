"""ENHANCED STATUS MONITORING TASKS WITH DOCKER SWARM INFORMATION"""

import logging

import rollbar

from gefapi.services.docker_service import get_docker_client

logger = logging.getLogger(__name__)


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



