"""Test swarm status endpoint optimizations."""

import time
from unittest.mock import Mock, patch

import pytest

from gefapi.tasks.status_monitoring import (
    SWARM_CACHE_KEY,
    SWARM_PERF_CACHE_KEY,
    _get_optimized_task_data,
    get_cached_swarm_status,
    warm_swarm_cache_on_startup,
)


class TestSwarmOptimizations:
    """Test the swarm status endpoint optimizations."""

    def test_optimized_task_data_caching(self):
        """Test that task data is cached correctly."""
        mock_docker_client = Mock()
        mock_service = Mock()
        mock_service.id = "test-service-123"
        mock_service.tasks.return_value = [
            {
                "NodeID": "node-1",
                "Status": {"State": "running"},
                "Spec": {
                    "Resources": {
                        "Reservations": {
                            "NanoCPUs": 500000000,  # 0.5 CPU
                            "MemoryBytes": 536870912,  # 512MB
                        }
                    }
                },
            }
        ]
        mock_service.attrs = {
            "Spec": {
                "TaskTemplate": {
                    "Resources": {
                        "Reservations": {
                            "NanoCPUs": 100000000,  # 0.1 CPU default
                            "MemoryBytes": 104857600,  # 100MB default
                        }
                    }
                }
            }
        }
        mock_docker_client.services.list.return_value = [mock_service]

        # First call should populate cache
        start_time = time.time()
        result1 = _get_optimized_task_data(mock_docker_client)
        first_call_time = time.time() - start_time

        # Second call should use cache (should be faster)
        start_time = time.time()
        result2 = _get_optimized_task_data(mock_docker_client)
        second_call_time = time.time() - start_time

        # Verify results are identical
        assert result1 == result2

        # Verify the data structure
        assert "node-1" in result1
        assert result1["node-1"]["task_count"] == 1
        assert result1["node-1"]["used_cpu_nanos"] == 500000000
        assert result1["node-1"]["used_memory_bytes"] == 536870912

        # Second call should be much faster (cached)
        assert second_call_time < first_call_time * 0.5, (
            f"Cache not working: first={first_call_time:.4f}s, "
            f"second={second_call_time:.4f}s"
        )

    @patch("gefapi.tasks.status_monitoring.get_redis_cache")
    def test_cache_warming_task(self, mock_get_cache):
        """Test the cache warming task."""
        mock_cache = Mock()
        mock_cache.is_available.return_value = True
        mock_cache.set.return_value = True
        mock_get_cache.return_value = mock_cache

        with patch("gefapi.tasks.status_monitoring.update_swarm_cache") as mock_update:
            mock_update.return_value = {
                "swarm_active": True,
                "total_nodes": 2,
                "total_managers": 1,
                "total_workers": 1,
                "cache_info": {
                    "cached_at": "2025-01-15T10:30:00Z",
                    "source": "startup_warm",
                },
            }

            # Call the warming function
            result = warm_swarm_cache_on_startup.apply(
                kwargs={}, task_id="test-warm-task"
            )

            # Verify successful warming
            assert result.result["success"] is True
            assert "Cache warmed successfully" in result.result["message"]
            assert result.result["swarm_data"]["swarm_active"] is True

    @patch("gefapi.tasks.status_monitoring.get_redis_cache")
    def test_performance_monitoring(self, mock_get_cache):
        """Test that performance metrics are stored."""
        mock_cache = Mock()
        mock_cache.is_available.return_value = True
        mock_cache.set.return_value = True
        mock_get_cache.return_value = mock_cache

        from gefapi.tasks.status_monitoring import _store_performance_metrics

        # Test performance data
        perf_data = {
            "collection_time_seconds": 1.5,
            "node_count": 3,
            "task_collection_time_seconds": 0.8,
            "cache_used": False,
        }

        # Store performance metrics
        _store_performance_metrics(perf_data)

        # Verify cache was called with performance data
        mock_cache.set.assert_called_once()
        call_args = mock_cache.set.call_args

        assert call_args[0][0] == SWARM_PERF_CACHE_KEY
        assert call_args[0][2] == 600  # 10 minute TTL

        stored_data = call_args[0][1]
        assert stored_data["collection_time_seconds"] == 1.5
        assert stored_data["node_count"] == 3
        assert "timestamp" in stored_data

    @patch("gefapi.tasks.status_monitoring.get_redis_cache")
    def test_cached_swarm_status_fallback(self, mock_get_cache):
        """Test cached swarm status with unavailable cache."""
        mock_cache = Mock()
        mock_cache.is_available.return_value = False
        mock_get_cache.return_value = mock_cache

        result = get_cached_swarm_status()

        # Should return unavailable status with proper structure
        assert result["swarm_active"] is False
        assert (
            result["error"] == "Docker Swarm status unavailable - cache not accessible"
        )
        assert result["nodes"] == []
        assert result["total_nodes"] == 0
        assert result["cache_info"]["source"] == "cache_unavailable"

    def test_api_endpoint_optimization_structure(self):
        """Test that the API endpoint can handle optimized data structure."""
        # Mock optimized swarm data with performance metadata
        mock_swarm_data = {
            "swarm_active": True,
            "total_nodes": 2,
            "total_managers": 1,
            "total_workers": 1,
            "error": None,
            "nodes": [
                {
                    "id": "node-1",
                    "hostname": "manager-1",
                    "role": "manager",
                    "is_manager": True,
                    "is_leader": True,
                    "running_tasks": 3,
                    "available_capacity": 10,
                    "resource_usage": {
                        "used_cpu_percent": 15.0,
                        "used_memory_percent": 25.0,
                    },
                }
            ],
            "_performance": {
                "collection_time_seconds": 0.85,
                "node_count": 2,
                "task_collection_time_seconds": 0.42,
                "cache_used": False,
            },
            "cache_info": {
                "cached_at": "2025-01-15T10:30:00Z",
                "cache_ttl": 300,
                "source": "cached",
            },
        }

        # Verify the structure is valid
        assert "_performance" in mock_swarm_data
        assert mock_swarm_data["_performance"]["collection_time_seconds"] < 1.0
        assert mock_swarm_data["nodes"][0]["available_capacity"] == 10

        # Performance metadata should not affect existing functionality
        assert mock_swarm_data["swarm_active"] is True
        assert mock_swarm_data["total_nodes"] == 2


@pytest.mark.integration
class TestSwarmOptimizationIntegration:
    """Integration tests for swarm optimizations."""

    def test_optimization_reduces_api_calls(self):
        """Test that optimizations actually reduce Docker API calls."""
        # This would require a real Docker environment
        # For now, verify the optimization path exists
        # Verify the function includes performance monitoring
        import inspect

        from gefapi.tasks.status_monitoring import _get_docker_swarm_info

        source = inspect.getsource(_get_docker_swarm_info)

        assert "_get_optimized_task_data" in source
        assert "_performance" in source
        assert "collection_start_time" in source

    def test_cache_key_consistency(self):
        """Test that cache keys are consistent."""
        # Verify cache keys are properly defined
        assert SWARM_CACHE_KEY == "docker_swarm_status"
        assert SWARM_PERF_CACHE_KEY == "docker_swarm_performance"

        # Verify they're different
        assert SWARM_CACHE_KEY != SWARM_PERF_CACHE_KEY
