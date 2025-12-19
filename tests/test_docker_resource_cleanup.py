"""Tests for Docker resource cleanup tasks"""

from unittest.mock import MagicMock, patch

from gefapi.tasks import docker_resource_cleanup


def _run_task(task):
    return task.apply().result


def run_cleanup_docker_build_cache():
    return _run_task(docker_resource_cleanup.cleanup_docker_build_cache)


class TestDockerResourceCleanup:
    """Test Docker resource cleanup functionality"""

    def test_cleanup_with_no_docker_client(self, app):
        """Test cleanup when Docker client is not available"""
        with (
            app.app_context(),
            patch(
                "gefapi.tasks.docker_resource_cleanup.get_docker_client"
            ) as mock_docker,
        ):
            mock_docker.return_value = None

            result = run_cleanup_docker_build_cache()

            assert result["success"] is False
            assert result["error"] == "Docker client not available"
            assert result["build_cache_pruned"] is False
            assert result["images_pruned"] == 0
            assert result["space_reclaimed_bytes"] == 0

    def test_cleanup_dangling_images(self, app):
        """Test cleanup successfully prunes dangling images"""
        with (
            app.app_context(),
            patch(
                "gefapi.tasks.docker_resource_cleanup.get_docker_client"
            ) as mock_docker,
        ):
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            # Mock dangling image prune (only one call now)
            mock_client.images.prune.return_value = {
                "ImagesDeleted": [
                    {"Deleted": "sha256:abc123"},
                    {"Deleted": "sha256:def456"},
                ],
                "SpaceReclaimed": 1024 * 1024 * 100,  # 100 MB
            }

            # Mock other prune operations
            mock_client.containers.prune.return_value = {
                "ContainersDeleted": [],
                "SpaceReclaimed": 0,
            }
            mock_client.volumes.prune.return_value = {
                "VolumesDeleted": [],
                "SpaceReclaimed": 0,
            }
            mock_client.networks.prune.return_value = {
                "NetworksDeleted": [],
            }

            # Mock API for build cache prune
            mock_api = MagicMock()
            mock_client.api = mock_api
            mock_response = MagicMock()
            mock_api._post.return_value = mock_response
            mock_api._result.return_value = {
                "CachesDeleted": [],
                "SpaceReclaimed": 0,
            }

            result = run_cleanup_docker_build_cache()

            assert result["success"] is True
            assert result["images_pruned"] == 2
            assert result["space_reclaimed_bytes"] >= 1024 * 1024 * 100

    def test_cleanup_build_cache(self, app):
        """Test cleanup successfully prunes build cache"""
        with (
            app.app_context(),
            patch(
                "gefapi.tasks.docker_resource_cleanup.get_docker_client"
            ) as mock_docker,
        ):
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            # Mock image prune operations
            mock_client.images.prune.return_value = {
                "ImagesDeleted": [],
                "SpaceReclaimed": 0,
            }
            mock_client.containers.prune.return_value = {
                "ContainersDeleted": [],
                "SpaceReclaimed": 0,
            }
            mock_client.volumes.prune.return_value = {
                "VolumesDeleted": [],
                "SpaceReclaimed": 0,
            }
            mock_client.networks.prune.return_value = {
                "NetworksDeleted": [],
            }

            # Mock API for build cache prune with some reclaimed space
            mock_api = MagicMock()
            mock_client.api = mock_api
            mock_response = MagicMock()
            mock_api._post.return_value = mock_response
            mock_api._result.return_value = {
                "CachesDeleted": ["cache1", "cache2", "cache3"],
                "SpaceReclaimed": 1024 * 1024 * 500,  # 500 MB
            }

            result = run_cleanup_docker_build_cache()

            assert result["success"] is True
            assert result["build_cache_pruned"] is True
            assert result["space_reclaimed_bytes"] >= 1024 * 1024 * 500
            # Verify _post was called with build/prune endpoint
            mock_api._post.assert_called()

    def test_cleanup_handles_docker_errors_gracefully(self, app):
        """Test cleanup continues when individual prune operations fail"""
        with (
            app.app_context(),
            patch(
                "gefapi.tasks.docker_resource_cleanup.get_docker_client"
            ) as mock_docker,
        ):
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            # Make dangling image prune fail
            mock_client.images.prune.side_effect = Exception("Docker error")

            # Mock other operations to succeed
            mock_client.containers.prune.return_value = {
                "ContainersDeleted": ["container1"],
                "SpaceReclaimed": 1024 * 1024,
            }
            mock_client.volumes.prune.return_value = {
                "VolumesDeleted": [],
                "SpaceReclaimed": 0,
            }
            mock_client.networks.prune.return_value = {
                "NetworksDeleted": [],
            }

            # Mock API for build cache prune - make it also fail
            mock_api = MagicMock()
            mock_client.api = mock_api
            mock_api._post.side_effect = Exception("Build cache error")

            result = run_cleanup_docker_build_cache()

            # Should still succeed overall but with partial results
            assert result["success"] is True
            # Build cache should still be marked as pruned due to container fallback
            assert result["build_cache_pruned"] is True

    def test_cleanup_prunes_volumes_and_networks(self, app):
        """Test cleanup prunes volumes and networks"""
        with (
            app.app_context(),
            patch(
                "gefapi.tasks.docker_resource_cleanup.get_docker_client"
            ) as mock_docker,
        ):
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            # Mock image prune operations
            mock_client.images.prune.return_value = {
                "ImagesDeleted": [],
                "SpaceReclaimed": 0,
            }
            mock_client.containers.prune.return_value = {
                "ContainersDeleted": [],
                "SpaceReclaimed": 0,
            }
            # Mock volume prune with some reclaimed space
            mock_client.volumes.prune.return_value = {
                "VolumesDeleted": ["vol1", "vol2"],
                "SpaceReclaimed": 1024 * 1024 * 200,
            }
            # Mock network prune
            mock_client.networks.prune.return_value = {
                "NetworksDeleted": ["network1"],
            }

            # Mock API for build cache prune
            mock_api = MagicMock()
            mock_client.api = mock_api
            mock_response = MagicMock()
            mock_api._post.return_value = mock_response
            mock_api._result.return_value = {
                "CachesDeleted": [],
                "SpaceReclaimed": 0,
            }

            result = run_cleanup_docker_build_cache()

            assert result["success"] is True
            assert result["space_reclaimed_bytes"] >= 1024 * 1024 * 200
            # Verify prune methods were called
            mock_client.volumes.prune.assert_called_once()
            mock_client.networks.prune.assert_called_once()

    def test_cleanup_space_reclaimed_human_readable_mb(self, app):
        """Test cleanup returns human-readable space for megabytes"""
        with (
            app.app_context(),
            patch(
                "gefapi.tasks.docker_resource_cleanup.get_docker_client"
            ) as mock_docker,
        ):
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            # Mock with 500 MB reclaimed from dangling images
            mock_client.images.prune.return_value = {
                "ImagesDeleted": [{"Deleted": "sha256:abc123"}],
                "SpaceReclaimed": 1024 * 1024 * 500,  # 500 MB
            }
            mock_client.containers.prune.return_value = {
                "ContainersDeleted": [],
                "SpaceReclaimed": 0,
            }
            mock_client.volumes.prune.return_value = {
                "VolumesDeleted": [],
                "SpaceReclaimed": 0,
            }
            mock_client.networks.prune.return_value = {"NetworksDeleted": []}

            mock_api = MagicMock()
            mock_client.api = mock_api
            mock_api._post.return_value = MagicMock()
            mock_api._result.return_value = {
                "CachesDeleted": [],
                "SpaceReclaimed": 0,
            }

            result = run_cleanup_docker_build_cache()

            assert result["success"] is True
            assert "MB" in result["space_reclaimed_human"]

    def test_cleanup_space_reclaimed_human_readable_gb(self, app):
        """Test cleanup returns human-readable space for gigabytes"""
        with (
            app.app_context(),
            patch(
                "gefapi.tasks.docker_resource_cleanup.get_docker_client"
            ) as mock_docker,
        ):
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            # Mock with 2 GB reclaimed from dangling images
            mock_client.images.prune.return_value = {
                "ImagesDeleted": [{"Deleted": "sha256:abc123"}],
                "SpaceReclaimed": 1024 * 1024 * 1024 * 2,  # 2 GB
            }
            mock_client.containers.prune.return_value = {
                "ContainersDeleted": [],
                "SpaceReclaimed": 0,
            }
            mock_client.volumes.prune.return_value = {
                "VolumesDeleted": [],
                "SpaceReclaimed": 0,
            }
            mock_client.networks.prune.return_value = {"NetworksDeleted": []}

            mock_api = MagicMock()
            mock_client.api = mock_api
            mock_api._post.return_value = MagicMock()
            mock_api._result.return_value = {
                "CachesDeleted": [],
                "SpaceReclaimed": 0,
            }

            result = run_cleanup_docker_build_cache()

            assert result["success"] is True
            assert "GB" in result["space_reclaimed_human"]
