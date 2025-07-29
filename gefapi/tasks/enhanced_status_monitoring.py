"""ENHANCED STATUS MONITORING TASKS WITH DOCKER SWARM INFORMATION"""

import contextlib
import logging

from celery import Task
import psutil
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


@celery.task(base=EnhancedStatusMonitoringTask, bind=True)
def collect_enhanced_system_status(self):
    """
    Collect enhanced system status including Docker Swarm node information.
    This extends the original status collection with detailed swarm metrics.
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

            # Get system metrics
            logger.info("[TASK]: Collecting system metrics")
            memory = psutil.virtual_memory()
            memory_available_percent = memory.available / memory.total * 100
            cpu_usage_percent = psutil.cpu_percent(interval=1)

            logger.info(
                f"[TASK]: System metrics - CPU: {cpu_usage_percent}%, "
                f"Memory Available: {memory_available_percent:.1f}%"
            )

            # NEW: Collect Docker Swarm information
            logger.info("[TASK]: Collecting Docker Swarm node information")
            swarm_info = _get_docker_swarm_info()
            logger.info(
                f"[TASK]: Docker Swarm - Active: {swarm_info['swarm_active']}, "
                f"Nodes: {swarm_info['total_nodes']}, "
                f"Managers: {swarm_info['total_managers']}, "
                f"Workers: {swarm_info['total_workers']}"
            )

            # Create status log entry (original fields)
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
                memory_available_percent=memory_available_percent,
                cpu_usage_percent=cpu_usage_percent,
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
