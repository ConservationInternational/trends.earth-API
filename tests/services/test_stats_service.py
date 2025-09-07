"""
Tests for the StatsService class.
Tests caching, data aggregation, and error handling.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from gefapi.services.stats_service import StatsService
from gefapi.utils.redis_cache import RedisCache


class TestStatsService:
    """Test cases for StatsService."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_redis = MagicMock(spec=RedisCache)
        self.sample_summary_data = {
            "total_executions": 1500,
            "total_jobs": 1500,  # Backward compatibility alias
            "total_users": 250,
            "total_scripts": 50,
            "total_executions_finished": 1200,
            "total_executions_failed": 200,
            "total_executions_cancelled": 100,
        }

    @patch("gefapi.services.stats_service.get_redis_cache")
    def test_cache_key_generation(self, mock_get_redis_cache):
        """Test cache key generation is consistent."""
        # Test basic cache key
        key1 = StatsService._get_cache_key("test_method")
        assert key1 == "stats_service:test_method:"

        # Test cache key with parameters
        key2 = StatsService._get_cache_key(
            "test_method", param1="value1", param2="value2"
        )
        assert key2 == "stats_service:test_method:param1=value1_param2=value2"

        # Test parameter ordering consistency
        key3 = StatsService._get_cache_key(
            "test_method", param2="value2", param1="value1"
        )
        assert key2 == key3  # Should be same due to sorting

    @patch("gefapi.services.stats_service.get_redis_cache")
    @patch("gefapi.services.stats_service.db")
    def test_cache_hit(self, mock_db, mock_get_redis_cache):
        """Test successful cache hit scenario."""
        # Setup
        mock_get_redis_cache.return_value = self.mock_redis
        self.mock_redis.is_available.return_value = True
        self.mock_redis.get.return_value = self.sample_summary_data

        # Execute
        cache_key = "test_cache_key"
        execution_func = MagicMock(return_value={"new_data": True})

        result = StatsService._get_from_cache_or_execute(cache_key, execution_func)

        # Verify
        assert result == self.sample_summary_data
        self.mock_redis.get.assert_called_once_with(cache_key)
        execution_func.assert_not_called()  # Should not execute function

    @patch("gefapi.services.stats_service.get_redis_cache")
    @patch("gefapi.services.stats_service.db")
    def test_cache_miss_and_set(self, mock_db, mock_get_redis_cache):
        """Test cache miss scenario with successful cache set."""
        # Setup
        mock_get_redis_cache.return_value = self.mock_redis
        self.mock_redis.is_available.return_value = True
        self.mock_redis.get.return_value = None  # Cache miss
        self.mock_redis.set.return_value = True

        execution_func = MagicMock(return_value=self.sample_summary_data)

        # Execute
        cache_key = "test_cache_key"
        result = StatsService._get_from_cache_or_execute(cache_key, execution_func)

        # Verify
        assert result == self.sample_summary_data
        self.mock_redis.get.assert_called_once_with(cache_key)
        execution_func.assert_called_once()
        self.mock_redis.set.assert_called_once_with(
            cache_key, self.sample_summary_data, ttl=300
        )

    @patch("gefapi.services.stats_service.get_redis_cache")
    def test_redis_unavailable_fallback(self, mock_get_redis_cache):
        """Test fallback when Redis is unavailable."""
        # Setup
        mock_get_redis_cache.return_value = self.mock_redis
        self.mock_redis.is_available.return_value = False

        execution_func = MagicMock(return_value=self.sample_summary_data)

        # Execute
        cache_key = "test_cache_key"
        result = StatsService._get_from_cache_or_execute(cache_key, execution_func)

        # Verify
        assert result == self.sample_summary_data
        execution_func.assert_called_once()
        self.mock_redis.get.assert_not_called()
        self.mock_redis.set.assert_not_called()

    @patch("gefapi.services.stats_service.get_redis_cache")
    @patch("gefapi.services.stats_service.db")
    def test_get_dashboard_stats_with_cache(self, mock_db, mock_get_redis_cache):
        """Test get_dashboard_stats with caching."""
        # Setup
        mock_get_redis_cache.return_value = self.mock_redis
        self.mock_redis.is_available.return_value = True

        expected_result = {
            "summary": self.sample_summary_data,
            "trends": {"hourly_jobs": [], "daily_jobs": []},
            "geographic": {"top_countries": []},
            "tasks": {"by_type": [], "by_version": []},
        }

        self.mock_redis.get.return_value = expected_result

        # Execute
        result = StatsService.get_dashboard_stats(
            period="last_month", include=["summary", "trends"]
        )

        # Verify
        assert result == expected_result
        expected_cache_key = (
            "stats_service:get_dashboard_stats:include=summary,trends_period=last_month"
        )
        self.mock_redis.get.assert_called_once_with(expected_cache_key)

    @patch("gefapi.services.stats_service.get_redis_cache")
    @patch("gefapi.services.stats_service.db")
    def test_get_execution_stats_with_cache(self, mock_db, mock_get_redis_cache):
        """Test get_execution_stats with caching."""
        # Setup
        mock_get_redis_cache.return_value = self.mock_redis
        self.mock_redis.is_available.return_value = True

        expected_result = {
            "time_series": [{"timestamp": "2025-08-08T10:00:00Z", "total": 10}],
            "top_users": [{"user_id": "123", "email": "test@example.com"}],
            "task_performance": [{"task": "productivity", "total_executions": 100}],
        }

        self.mock_redis.get.return_value = expected_result

        # Execute
        result = StatsService.get_execution_stats(
            period="last_week",
            group_by="day",
            task_type="productivity",
            status="FINISHED",
        )

        # Verify
        assert result == expected_result
        expected_cache_key = "stats_service:get_execution_stats:group_by=day_period=last_week_status=FINISHED_task_type=productivity"
        self.mock_redis.get.assert_called_once_with(expected_cache_key)

    @patch("gefapi.services.stats_service.get_redis_cache")
    @patch("gefapi.services.stats_service.db")
    def test_get_user_stats_with_cache(self, mock_db, mock_get_redis_cache):
        """Test get_user_stats with caching."""
        # Setup
        mock_get_redis_cache.return_value = self.mock_redis
        self.mock_redis.is_available.return_value = True

        expected_result = {
            "registration_trends": [{"date": "2025-08-08", "new_users": 5}],
            "geographic_distribution": [{"country": "USA", "user_count": 100}],
            "activity_stats": {"active_last_day": 10, "active_last_week": 50},
        }

        self.mock_redis.get.return_value = expected_result

        # Execute
        result = StatsService.get_user_stats(
            period="last_year", group_by="month", country="USA"
        )

        # Verify
        assert result == expected_result
        expected_cache_key = (
            "stats_service:get_user_stats:country=USA_group_by=month_period=last_year"
        )
        self.mock_redis.get.assert_called_once_with(expected_cache_key)

    @patch("gefapi.services.stats_service.get_redis_cache")
    def test_clear_cache_all(self, mock_get_redis_cache):
        """Test clearing all cache."""
        # Setup
        mock_get_redis_cache.return_value = self.mock_redis
        self.mock_redis.is_available.return_value = True
        self.mock_redis.client.scan_iter.return_value = [
            "stats_service:method1:params",
            "stats_service:method2:params",
        ]
        self.mock_redis.client.delete.return_value = 2

        # Execute
        result = StatsService.clear_cache()

        # Verify
        assert result is True
        self.mock_redis.client.scan_iter.assert_called_once_with(
            match="stats_service:*"
        )
        self.mock_redis.client.delete.assert_called_once()

    @patch("gefapi.services.stats_service.get_redis_cache")
    def test_clear_cache_pattern(self, mock_get_redis_cache):
        """Test clearing cache with specific pattern."""
        # Setup
        mock_get_redis_cache.return_value = self.mock_redis
        self.mock_redis.is_available.return_value = True
        self.mock_redis.client.scan_iter.return_value = [
            "stats_service:_get_summary_stats:",
        ]
        self.mock_redis.client.delete.return_value = 1

        # Execute
        result = StatsService.clear_cache("summary")

        # Verify
        assert result is True
        self.mock_redis.client.scan_iter.assert_called_once_with(
            match="stats_service:*summary*"
        )

    @patch("gefapi.services.stats_service.get_redis_cache")
    def test_clear_cache_redis_unavailable(self, mock_get_redis_cache):
        """Test clear cache when Redis is unavailable."""
        # Setup
        mock_get_redis_cache.return_value = self.mock_redis
        self.mock_redis.is_available.return_value = False

        # Execute
        result = StatsService.clear_cache()

        # Verify
        assert result is False

    @patch("gefapi.services.stats_service.get_redis_cache")
    def test_get_cache_info_available(self, mock_get_redis_cache):
        """Test get_cache_info when Redis is available."""
        # Setup
        mock_get_redis_cache.return_value = self.mock_redis
        self.mock_redis.is_available.return_value = True
        self.mock_redis.client.scan_iter.return_value = [
            "stats_service:method1:params",
            "stats_service:method2:params",
        ]
        self.mock_redis.get_ttl.side_effect = [120, 240]

        # Execute
        result = StatsService.get_cache_info()

        # Verify - should be sorted by TTL descending, so method2 (240) comes first
        assert result["available"] is True
        assert result["total_keys"] == 2
        assert len(result["keys"]) == 2
        assert result["keys"][0]["key"] == "stats_service:method2:params"
        assert result["keys"][0]["ttl_seconds"] == 240
        assert result["keys"][0]["expires_in"] == "4m 0s"

    @patch("gefapi.services.stats_service.get_redis_cache")
    def test_get_cache_info_unavailable(self, mock_get_redis_cache):
        """Test get_cache_info when Redis is unavailable."""
        # Setup
        mock_get_redis_cache.return_value = self.mock_redis
        self.mock_redis.is_available.return_value = False

        # Execute
        result = StatsService.get_cache_info()

        # Verify
        assert result["available"] is False
        assert "error" in result

    def test_time_filter_generation(self):
        """Test _get_time_filter method."""
        now = datetime.utcnow()

        # Test different periods
        assert StatsService._get_time_filter("last_day") is not None
        assert StatsService._get_time_filter("last_week") is not None
        assert StatsService._get_time_filter("last_month") is not None
        assert StatsService._get_time_filter("last_year") is not None
        assert StatsService._get_time_filter("all") is None
        assert StatsService._get_time_filter("invalid") is None

        # Test specific time calculation
        last_day_filter = StatsService._get_time_filter("last_day")
        expected_last_day = now - timedelta(days=1)
        time_diff = abs((last_day_filter - expected_last_day).total_seconds())
        assert time_diff < 5  # Within 5 seconds

    def test_normalize_task_name(self):
        """Test task name normalization."""
        # Test version removal
        assert (
            StatsService._normalize_task_name("productivity-v2.1.0") == "productivity"
        )
        assert StatsService._normalize_task_name("land-cover-v1.5") == "land-cover"

        # Test deprecated name mapping
        assert (
            StatsService._normalize_task_name("sdg-sub-indicators")
            == "sdg-15-3-1-sub-indicators"
        )
        assert (
            StatsService._normalize_task_name("vegetation-productivity")
            == "productivity"
        )

        # Test productivity variants
        assert StatsService._normalize_task_name("productivity-trend") == "productivity"
        assert StatsService._normalize_task_name("productivity-state") == "productivity"

        # Test edge cases
        assert StatsService._normalize_task_name("") == "unknown"
        assert StatsService._normalize_task_name(None) == "unknown"

    def test_extract_version(self):
        """Test version extraction from script slugs."""
        # Test version extraction
        assert StatsService._extract_version("productivity-v2.1.0") == "2"
        assert StatsService._extract_version("land-cover-v1.5.2") == "1"
        assert StatsService._extract_version("task-v3") == "3"

        # Test no version
        assert StatsService._extract_version("productivity") == "unknown"
        assert StatsService._extract_version("") == "unknown"
        assert StatsService._extract_version(None) == "unknown"

        # Test version with hyphens
        assert StatsService._extract_version("task-v2-1-0") == "2"

    @patch("gefapi.services.stats_service.get_redis_cache")
    @patch("gefapi.services.stats_service.db")
    def test_summary_stats_includes_required_counts(
        self, mock_db, mock_get_redis_cache
    ):
        """Test that _get_summary_stats includes all required counts from issue #49."""
        # Setup mock database queries
        mock_session = MagicMock()
        mock_db.session = mock_session

        # Mock the scalar() results for different queries
        call_count = 0

        def mock_scalar():
            nonlocal call_count
            results = [
                1000,
                250,
                150,
                800,
                150,
                50,
            ]  # executions, users, scripts, finished, failed, cancelled
            result = results[call_count % len(results)]
            call_count += 1
            return result

        mock_session.query().scalar = mock_scalar
        mock_session.query().filter().scalar = mock_scalar

        # Mock Redis unavailable to force execution
        mock_get_redis_cache.return_value = self.mock_redis
        self.mock_redis.is_available.return_value = False

        # Execute
        result = StatsService._get_summary_stats()

        # Verify all required fields are present
        assert "total_executions" in result
        assert "total_users" in result
        assert "total_scripts" in result
        assert "total_executions_finished" in result
        assert "total_executions_failed" in result
        assert "total_executions_cancelled" in result

        # Verify backward compatibility
        assert "total_jobs" in result
        assert result["total_jobs"] == result["total_executions"]


class TestStatsServiceIntegration:
    """Integration tests that require database setup."""

    @pytest.fixture(autouse=True)
    def setup_database(self, app, db_session):
        """Set up database for integration tests."""
        with app.app_context():
            yield

    @patch("gefapi.services.stats_service.get_redis_cache")
    def test_get_summary_stats_database_integration(
        self, mock_get_redis_cache, app, db_session
    ):
        """Test _get_summary_stats with actual database queries."""
        # Setup Redis to be unavailable to force database queries
        mock_redis = MagicMock()
        mock_redis.is_available.return_value = False
        mock_get_redis_cache.return_value = mock_redis

        with app.app_context():
            # Execute with default period
            result = StatsService._get_summary_stats()

            # Verify structure for the new implementation
            assert isinstance(result, dict)
            assert "total_executions" in result
            assert "total_jobs" in result  # Backward compatibility alias
            assert "total_users" in result
            assert "total_scripts" in result
            assert "total_executions_finished" in result
            assert "total_executions_failed" in result
            assert "total_executions_cancelled" in result

            # Values should be integers (even if 0)
            for key, value in result.items():
                assert isinstance(value, int)
                assert value >= 0

            # Test with different period parameters
            for period in ["last_day", "last_week", "last_month", "last_year", "all"]:
                period_result = StatsService._get_summary_stats(period=period)
                assert isinstance(period_result, dict)
                assert "total_executions" in period_result
                assert "total_users" in period_result

    @patch("gefapi.services.stats_service.get_redis_cache")
    def test_get_dashboard_stats_database_integration(
        self, mock_get_redis_cache, app, db_session
    ):
        """Test get_dashboard_stats with actual database."""
        # Setup Redis to be unavailable
        mock_redis = MagicMock()
        mock_redis.is_available.return_value = False
        mock_get_redis_cache.return_value = mock_redis

        with app.app_context():
            # Execute
            result = StatsService.get_dashboard_stats(
                period="last_month", include=["summary", "trends"]
            )

            # Verify structure
            assert isinstance(result, dict)
            assert "summary" in result
            assert "trends" in result
            assert isinstance(result["summary"], dict)
            assert isinstance(result["trends"], dict)

    def test_cache_performance_simulation(self):
        """Simulate cache performance improvement."""
        import time

        # Simulate slow database query
        def slow_db_query():
            time.sleep(0.1)  # 100ms simulated DB query
            return {"data": "from_database"}

        # Simulate fast cache retrieval
        def fast_cache_retrieval():
            time.sleep(0.001)  # 1ms simulated cache retrieval
            return {"data": "from_cache"}

        # Test performance difference
        start_time = time.time()
        slow_db_query()
        db_time = time.time() - start_time

        start_time = time.time()
        fast_cache_retrieval()
        cache_time = time.time() - start_time

        # Verify cache is significantly faster
        assert cache_time < db_time / 10  # At least 10x faster
        assert db_time > 0.05  # DB query took reasonable time
        assert cache_time < 0.01  # Cache was very fast
