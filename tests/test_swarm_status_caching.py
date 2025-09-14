"""
Tests for Docker Swarm status caching functionality.

Tests the swarm status endpoint caching, Redis integration, and performance optimizations.
"""

import datetime
from unittest.mock import patch

import pytest

from gefapi.tasks.status_monitoring import (
    SWARM_CACHE_BACKUP_KEY,
    SWARM_CACHE_KEY,
    get_cached_swarm_status,
    refresh_swarm_cache_task,
    update_swarm_cache,
)


@pytest.mark.usefixtures("client", "auth_headers_admin")
class TestSwarmStatusEndpoint:
    """Test swarm status endpoint functionality and caching"""

    def test_swarm_endpoint_requires_auth(self, client):
        """Test that swarm status endpoint requires authentication"""
        response = client.get("/api/v1/status/swarm")
        assert response.status_code == 401

    def test_swarm_endpoint_requires_admin(self, client, auth_headers_user):
        """Test that swarm status endpoint requires admin privileges"""
        response = client.get("/api/v1/status/swarm", headers=auth_headers_user)
        assert response.status_code == 403

    @patch("gefapi.tasks.status_monitoring.get_cached_swarm_status")
    def test_swarm_endpoint_returns_cached_data(
        self, mock_cached_status, client, auth_headers_admin
    ):
        """Test that swarm endpoint returns cached data structure"""
        # Mock cached swarm data
        mock_cached_status.return_value = {
            "swarm_active": True,
            "total_nodes": 2,
            "total_managers": 1,
            "total_workers": 1,
            "error": None,
            "nodes": [
                {
                    "id": "node-123",
                    "hostname": "manager-1",
                    "role": "manager",
                    "is_manager": True,
                    "is_leader": True,
                    "availability": "active",
                    "state": "ready",
                    "cpu_count": 4.0,
                    "memory_gb": 8.0,
                    "running_tasks": 2,
                    "available_capacity": 38,
                    "resource_usage": {
                        "used_cpu_nanos": 200000000,
                        "used_memory_bytes": 524288000,
                        "available_cpu_nanos": 3800000000,
                        "available_memory_bytes": 7634903040,
                        "used_cpu_percent": 5.0,
                        "used_memory_percent": 6.25,
                    },
                    "labels": {"node.role": "manager"},
                    "created_at": "2025-01-15T10:30:00Z",
                    "updated_at": "2025-01-15T10:30:00Z",
                }
            ],
            "cache_info": {
                "cached_at": "2025-01-15T10:30:00Z",
                "cache_ttl": 300,
                "cache_key": "docker_swarm_status",
                "source": "cached",
                "cache_hit": True,
            },
        }

        response = client.get("/api/v1/status/swarm", headers=auth_headers_admin)

        assert response.status_code == 200
        data = response.json

        # Verify response structure (new structured format)
        assert "message" in data
        assert "data" in data
        assert data["message"] == "Docker Swarm status retrieved successfully from cache"
        
        swarm_data = data["data"]

        # Verify swarm information
        assert swarm_data["swarm_active"] is True
        assert swarm_data["total_nodes"] == 2
        assert swarm_data["total_managers"] == 1
        assert swarm_data["total_workers"] == 1

        # Verify cache metadata
        assert "cache_info" in swarm_data
        cache_info = swarm_data["cache_info"]
        assert cache_info["cache_hit"] is True
        assert cache_info["source"] == "cached"

        # Verify node structure
        assert "nodes" in swarm_data
        assert len(swarm_data["nodes"]) == 1
        node = swarm_data["nodes"][0]
        assert "resource_usage" in node
        assert "used_cpu_percent" in node["resource_usage"]

    @patch("gefapi.tasks.status_monitoring.get_cached_swarm_status")
    def test_swarm_endpoint_handles_cache_unavailable(
        self, mock_cached_status, client, auth_headers_admin
    ):
        """Test swarm endpoint when cache is unavailable"""
        # Mock cache unavailable scenario
        mock_cached_status.return_value = {
            "error": "Docker Swarm status unavailable - cache not accessible",
            "nodes": [],
            "total_nodes": 0,
            "total_managers": 0,
            "total_workers": 0,
            "swarm_active": False,
            "cache_info": {
                "cached_at": "2025-01-15T10:30:00Z",
                "cache_ttl": 0,
                "cache_key": "docker_swarm_status",
                "source": "cache_unavailable",
                "cache_hit": False,
            },
        }

        response = client.get("/api/v1/status/swarm", headers=auth_headers_admin)

        assert response.status_code == 200
        data = response.json

        # Verify response structure (new structured format)
        assert "message" in data
        assert "data" in data
        assert data["message"] == "Docker Swarm status unavailable - cache not accessible"
        
        swarm_data = data["data"]
        assert swarm_data["swarm_active"] is False
        assert "error" in swarm_data
        assert swarm_data["cache_info"]["cache_hit"] is False


@pytest.mark.usefixtures("app")
class TestSwarmStatusCaching:
    """Test swarm status caching functionality"""

    @patch("gefapi.utils.redis_cache.RedisCache.is_available")
    @patch("gefapi.utils.redis_cache.RedisCache.get")
    def test_get_cached_swarm_status_primary_cache_hit(
        self, mock_cache_get, mock_cache_available, app
    ):
        """Test primary cache hit scenario"""
        with app.app_context():
            # Mock Redis available
            mock_cache_available.return_value = True

            # Mock primary cache hit
            cached_data = {
                "swarm_active": True,
                "total_nodes": 1,
                "nodes": [],
                "cache_info": {
                    "cached_at": "2025-01-15T10:30:00Z",
                    "cache_ttl": 300,
                    "cache_key": SWARM_CACHE_KEY,
                    "source": "cached",
                },
            }
            mock_cache_get.return_value = cached_data

            result = get_cached_swarm_status()

            # Verify primary cache was used
            assert result["cache_info"]["cache_hit"] is True
            assert result["cache_info"]["source"] == "cached"
            mock_cache_get.assert_called_once_with(SWARM_CACHE_KEY)

    @patch("gefapi.utils.redis_cache.RedisCache.is_available")
    @patch("gefapi.utils.redis_cache.RedisCache.get")
    def test_get_cached_swarm_status_backup_cache_fallback(
        self, mock_cache_get, mock_cache_available, app
    ):
        """Test backup cache fallback when primary cache misses"""
        with app.app_context():
            # Mock Redis available
            mock_cache_available.return_value = True

            # Mock primary cache miss, backup cache hit
            backup_data = {
                "swarm_active": True,
                "total_nodes": 1,
                "nodes": [],
                "cache_info": {
                    "cached_at": "2025-01-15T10:25:00Z",
                    "cache_ttl": 1800,
                },
            }

            def cache_get_side_effect(key):
                if key == SWARM_CACHE_KEY:
                    return None  # Primary cache miss
                elif key == SWARM_CACHE_BACKUP_KEY:
                    return backup_data  # Backup cache hit
                return None

            mock_cache_get.side_effect = cache_get_side_effect

            result = get_cached_swarm_status()

            # Verify backup cache was used
            assert result["cache_info"]["cache_hit"] is True
            assert result["cache_info"]["source"] == "backup_cache"
            assert result["cache_info"]["cache_key"] == SWARM_CACHE_BACKUP_KEY

            # Verify both cache keys were checked
            assert mock_cache_get.call_count == 2

    @patch("gefapi.utils.redis_cache.RedisCache.is_available")
    def test_get_cached_swarm_status_cache_unavailable(self, mock_cache_available, app):
        """Test cache unavailable scenario"""
        with app.app_context():
            # Mock Redis unavailable
            mock_cache_available.return_value = False

            result = get_cached_swarm_status()

            # Verify unavailable status returned
            assert result["swarm_active"] is False
            assert "error" in result
            assert result["cache_info"]["cache_hit"] is False
            assert result["cache_info"]["source"] == "cache_unavailable"

    @patch("gefapi.tasks.status_monitoring._get_docker_swarm_info")
    @patch("gefapi.utils.redis_cache.RedisCache.is_available")
    @patch("gefapi.utils.redis_cache.RedisCache.set")
    def test_update_swarm_cache_success(
        self, mock_cache_set, mock_cache_available, mock_get_swarm_info, app
    ):
        """Test successful cache update with both primary and backup"""
        with app.app_context():
            # Mock Redis available
            mock_cache_available.return_value = True
            mock_cache_set.return_value = True

            # Mock swarm data
            mock_swarm_data = {
                "swarm_active": True,
                "total_nodes": 2,
                "total_managers": 1,
                "total_workers": 1,
                "error": None,
                "nodes": [],
            }
            mock_get_swarm_info.return_value = mock_swarm_data

            result = update_swarm_cache()

            # Verify cache metadata was added
            assert "cache_info" in result
            cache_info = result["cache_info"]
            assert "cached_at" in cache_info
            assert "cache_operations" in cache_info
            assert cache_info["backup_cached"] is True

            # Verify both primary and backup cache were updated
            assert "primary_cache_updated" in cache_info["cache_operations"]
            assert "backup_cache_updated" in cache_info["cache_operations"]

            # Verify Redis set was called twice (primary + backup)
            assert mock_cache_set.call_count == 2

    @patch("gefapi.tasks.status_monitoring._get_docker_swarm_info")
    @patch("gefapi.utils.redis_cache.RedisCache.is_available")
    @patch("gefapi.utils.redis_cache.RedisCache.set")
    def test_update_swarm_cache_skip_backup_on_error(
        self, mock_cache_set, mock_cache_available, mock_get_swarm_info, app
    ):
        """Test backup cache is skipped when swarm data has error"""
        with app.app_context():
            # Mock Redis available
            mock_cache_available.return_value = True
            mock_cache_set.return_value = True

            # Mock swarm data with error
            mock_swarm_data = {
                "error": "Docker unavailable",
                "swarm_active": False,
                "total_nodes": 0,
                "nodes": [],
            }
            mock_get_swarm_info.return_value = mock_swarm_data

            result = update_swarm_cache()

            # Verify backup cache was skipped
            cache_info = result["cache_info"]
            assert cache_info["backup_cached"] is False
            assert "backup_cache_skipped_error" in cache_info["cache_operations"]

            # Verify only primary cache was updated
            assert mock_cache_set.call_count == 1

    @patch("gefapi.tasks.status_monitoring.update_swarm_cache")
    def test_refresh_swarm_cache_task_success(self, mock_update_cache, app):
        """Test successful swarm cache refresh task"""
        with app.app_context():
            # Mock successful cache update
            mock_update_cache.return_value = {
                "swarm_active": True,
                "total_nodes": 2,
                "total_managers": 1,
                "total_workers": 1,
                "cache_info": {
                    "cached_at": "2025-01-15T10:30:00Z",
                    "cache_operations": [
                        "primary_cache_updated",
                        "backup_cache_updated",
                    ],
                    "backup_cached": True,
                },
            }

            # Execute task
            result = refresh_swarm_cache_task()

            # Verify task execution
            assert result["swarm_active"] is True
            assert "performance_metrics" in result

            # Verify performance metrics
            metrics = result["performance_metrics"]
            assert "refresh_duration_seconds" in metrics
            assert "refresh_timestamp" in metrics
            assert "cache_operations_count" in metrics
            assert metrics["backup_cache_available"] is True

    @patch("gefapi.tasks.status_monitoring.update_swarm_cache")
    def test_refresh_swarm_cache_task_error_handling(self, mock_update_cache, app):
        """Test swarm cache refresh task error handling"""
        with app.app_context():
            # Mock cache update failure
            mock_update_cache.side_effect = Exception("Cache update failed")

            # Execute task
            result = refresh_swarm_cache_task()

            # Verify error handling
            assert result["swarm_active"] is False
            assert "error" in result
            assert "Cache refresh failed" in result["error"]

            # Verify performance metrics include error info
            assert "performance_metrics" in result
            metrics = result["performance_metrics"]
            assert "error_occurred" in metrics
            assert metrics["error_occurred"] is True


@pytest.mark.usefixtures("app")
class TestSwarmStatusCacheIntegration:
    """Integration tests for swarm status caching"""

    @patch("gefapi.tasks.status_monitoring.get_docker_client")
    def test_swarm_cache_integration_no_docker(self, mock_get_client, app):
        """Test cache integration when Docker is not available"""
        with app.app_context():
            # Mock Docker unavailable
            mock_get_client.return_value = None

            # Update cache
            result = update_swarm_cache()

            # Verify error handling
            assert result["swarm_active"] is False
            assert result["error"] == "Docker unavailable"

            # Verify cache metadata
            assert "cache_info" in result
            cache_info = result["cache_info"]
            assert "cached_at" in cache_info

    def test_cache_metadata_structure(self, app):
        """Test that cache metadata has expected structure"""
        with app.app_context():
            # Get cached status (will return unavailable in test)
            result = get_cached_swarm_status()

            # Verify required cache metadata fields
            assert "cache_info" in result
            cache_info = result["cache_info"]

            required_fields = [
                "cached_at",
                "cache_ttl",
                "cache_key",
                "source",
                "cache_hit",
            ]

            for field in required_fields:
                assert field in cache_info, f"Missing required field: {field}"

            # Verify timestamp format
            assert isinstance(cache_info["cached_at"], str)
            # Should be ISO format timestamp
            datetime.datetime.fromisoformat(
                cache_info["cached_at"].replace("Z", "+00:00")
            )
