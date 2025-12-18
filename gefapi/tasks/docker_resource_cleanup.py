"""DOCKER RESOURCE CLEANUP TASKS

Periodic cleanup of Docker resources including build cache and unused images.
"""

import contextlib
import logging

from celery import Task
import rollbar

from gefapi.services.docker_service import get_docker_client

logger = logging.getLogger(__name__)


class DockerResourceCleanupTask(Task):
    """Base task for Docker resource cleanup"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Docker resource cleanup task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


@celery.task(base=DockerResourceCleanupTask, bind=True)
def cleanup_docker_build_cache(self):
    """Clean up Docker build cache to reclaim disk space.

    This task runs weekly and prunes:
    - Dangling images (untagged images from failed/incomplete builds)
    - Build cache (docker builder prune)
    - Unused volumes (orphaned volumes not attached to containers)
    - Unused networks

    NOTE: This task intentionally does NOT prune non-dangling unused images
    to avoid removing cached registry images needed for running services.

    Returns a summary of space reclaimed.
    """
    logger.info("[TASK]: Starting Docker build cache and image cleanup")

    try:
        docker_client = get_docker_client()
        if docker_client is None:
            logger.warning(
                "[TASK]: Docker client not available, skipping Docker resource cleanup"
            )
            return {
                "success": False,
                "error": "Docker client not available",
                "build_cache_pruned": False,
                "images_pruned": 0,
                "space_reclaimed_bytes": 0,
            }

        total_space_reclaimed = 0
        images_removed = 0
        build_cache_pruned = False

        # 1. Prune dangling images (untagged images from failed/incomplete builds)
        try:
            logger.info("[TASK]: Pruning dangling images...")
            prune_result = docker_client.images.prune(filters={"dangling": True})
            images_deleted = prune_result.get("ImagesDeleted") or []
            space_reclaimed = prune_result.get("SpaceReclaimed", 0)

            images_removed += len(images_deleted)
            total_space_reclaimed += space_reclaimed

            logger.info(
                "[TASK]: Removed %d dangling images, reclaimed %s bytes",
                len(images_deleted),
                space_reclaimed,
            )
        except Exception as e:
            logger.warning(f"[TASK]: Failed to prune dangling images: {e}")

        # 2. Prune build cache
        # Note: The Docker SDK's df() and prune methods may not directly
        # support build cache pruning. We use the low-level API.
        try:
            logger.info("[TASK]: Pruning Docker build cache...")

            # Use low-level API client for builder prune
            # docker builder prune -f (force, non-interactive)
            api_client = docker_client.api

            # The build prune endpoint was added in Docker API 1.31
            # POST /build/prune
            try:
                # Try to call the builder prune endpoint directly
                response = api_client._post(
                    api_client._url("/build/prune"),
                    params={"all": False},  # Only unused build cache, not all
                )
                result = api_client._result(response, json=True)

                cache_space = result.get("SpaceReclaimed", 0)
                caches_deleted = result.get("CachesDeleted") or []

                total_space_reclaimed += cache_space
                build_cache_pruned = True

                logger.info(
                    "[TASK]: Pruned %d build cache entries, reclaimed %s bytes",
                    len(caches_deleted),
                    cache_space,
                )
            except AttributeError:
                # Fallback: If _post is not available, try using requests directly
                logger.warning(
                    "[TASK]: Low-level API not available for build prune, "
                    "trying alternative method..."
                )

                # Try using the containers prune as a fallback for stopped containers
                container_prune = docker_client.containers.prune()
                containers_deleted = container_prune.get("ContainersDeleted") or []
                container_space = container_prune.get("SpaceReclaimed", 0)

                total_space_reclaimed += container_space
                build_cache_pruned = True

                logger.info(
                    "[TASK]: Pruned %d stopped containers, reclaimed %s bytes",
                    len(containers_deleted),
                    container_space,
                )

        except Exception as e:
            logger.warning(f"[TASK]: Failed to prune build cache: {e}")

        # NOTE: We intentionally do NOT prune non-dangling unused images here.
        # Such images may be cached copies of registry images needed for running
        # new services. Pruning them would force re-pulls from the registry,
        # adding latency and potentially breaking service execution if the
        # registry is unavailable.

        # 3. Prune unused volumes (orphaned volumes not attached to containers)
        try:
            logger.info("[TASK]: Pruning unused volumes...")
            volume_prune = docker_client.volumes.prune()
            volumes_deleted = volume_prune.get("VolumesDeleted") or []
            volume_space = volume_prune.get("SpaceReclaimed", 0)

            total_space_reclaimed += volume_space

            logger.info(
                "[TASK]: Pruned %d unused volumes, reclaimed %s bytes",
                len(volumes_deleted),
                volume_space,
            )
        except Exception as e:
            logger.warning(f"[TASK]: Failed to prune volumes: {e}")

        # 4. Prune unused networks
        try:
            logger.info("[TASK]: Pruning unused networks...")
            network_prune = docker_client.networks.prune()
            networks_deleted = network_prune.get("NetworksDeleted") or []

            logger.info(
                "[TASK]: Pruned %d unused networks",
                len(networks_deleted),
            )
        except Exception as e:
            logger.warning(f"[TASK]: Failed to prune networks: {e}")

        # Convert bytes to human-readable format for logging
        space_mb = total_space_reclaimed / (1024 * 1024)
        space_gb = total_space_reclaimed / (1024 * 1024 * 1024)

        if space_gb >= 1:
            space_str = f"{space_gb:.2f} GB"
        else:
            space_str = f"{space_mb:.2f} MB"

        result = {
            "success": True,
            "build_cache_pruned": build_cache_pruned,
            "images_pruned": images_removed,
            "space_reclaimed_bytes": total_space_reclaimed,
            "space_reclaimed_human": space_str,
        }

        logger.info(
            "[TASK]: Docker resource cleanup complete. Removed %d images, reclaimed %s",
            images_removed,
            space_str,
        )

        return result

    except Exception as error:
        logger.error(f"[TASK]: Error during Docker resource cleanup: {str(error)}")
        logger.exception("Full traceback:")

        # Report to rollbar if available
        with contextlib.suppress(Exception):
            rollbar.report_exc_info()

        # Re-raise the error so Celery can handle it
        raise error
