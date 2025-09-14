"""
Tests for stats cache refresh tasks.

Tests verify that the periodic cache refresh tasks are properly configured
and can execute without errors, including proper Flask application context handling.
"""

from unittest.mock import MagicMock, patch

import pytest

from gefapi.tasks.stats_cache_refresh import (
    refresh_dashboard_stats_cache,
    refresh_execution_stats_cache,
    refresh_user_stats_cache,
)


class TestStatsCacheRefreshTasks:
    """Test suite for stats cache refresh tasks."""

    @patch("gefapi.tasks.stats_cache_refresh.StatsService.get_dashboard_stats")
    @patch("gefapi.tasks.stats_cache_refresh.StatsService._get_cache_key")
    def test_refresh_dashboard_stats_cache_success(
        self, mock_get_cache_key, mock_get_dashboard_stats
    ):
        """Test successful dashboard stats cache refresh."""
        # Setup mocks
        mock_get_dashboard_stats.return_value = {"summary": {"total_executions": 100}}
        mock_get_cache_key.return_value = "test_cache_key"

        # Execute task
        result = refresh_dashboard_stats_cache.apply().result

        # Verify results
        assert result["total_refreshed"] == 8  # 8 configs
        assert result["successful"] == 8
        assert result["failed"] == 0
        assert len(result["cache_keys"]) == 8

        # Verify service was called for each config
        assert mock_get_dashboard_stats.call_count == 8

    @patch("gefapi.tasks.stats_cache_refresh.StatsService.get_execution_stats")
    @patch("gefapi.tasks.stats_cache_refresh.StatsService._get_cache_key")
    def test_refresh_execution_stats_cache_success(
        self, mock_get_cache_key, mock_get_execution_stats
    ):
        """Test successful execution stats cache refresh."""
        # Setup mocks
        mock_get_execution_stats.return_value = {"time_series": []}
        mock_get_cache_key.return_value = "test_cache_key"

        # Execute task
        result = refresh_execution_stats_cache.apply().result

        # Verify results
        assert result["total_refreshed"] == 4  # 4 configs
        assert result["successful"] == 4
        assert result["failed"] == 0
        assert len(result["cache_keys"]) == 4

        # Verify service was called for each config
        assert mock_get_execution_stats.call_count == 4

    @patch("gefapi.tasks.stats_cache_refresh.StatsService.get_user_stats")
    @patch("gefapi.tasks.stats_cache_refresh.StatsService._get_cache_key")
    def test_refresh_user_stats_cache_success(
        self, mock_get_cache_key, mock_get_user_stats
    ):
        """Test successful user stats cache refresh."""
        # Setup mocks
        mock_get_user_stats.return_value = {"registration_trends": []}
        mock_get_cache_key.return_value = "test_cache_key"

        # Execute task
        result = refresh_user_stats_cache.apply().result

        # Verify results
        assert result["total_refreshed"] == 3  # 3 configs
        assert result["successful"] == 3
        assert result["failed"] == 0
        assert len(result["cache_keys"]) == 3

        # Verify service was called for each config
        assert mock_get_user_stats.call_count == 3

    @patch("gefapi.tasks.stats_cache_refresh.StatsService.get_dashboard_stats")
    @patch("gefapi.tasks.stats_cache_refresh.logger")
    def test_refresh_dashboard_stats_cache_handles_errors(
        self, mock_logger, mock_get_dashboard_stats
    ):
        """Test dashboard stats cache refresh handles errors gracefully."""
        # Setup mock to raise exception
        mock_get_dashboard_stats.side_effect = Exception("Database error")

        # Execute task
        result = refresh_dashboard_stats_cache.apply().result

        # Verify error handling
        assert result["total_refreshed"] == 8  # All configs attempted
        assert result["successful"] == 0
        assert result["failed"] == 8
        assert len(result["cache_keys"]) == 0

        # Verify errors were logged
        assert mock_logger.error.call_count == 8

    @patch("gefapi.tasks.stats_cache_refresh.rollbar.report_exc_info")
    @patch("gefapi.tasks.stats_cache_refresh.StatsService.get_dashboard_stats")
    def test_refresh_dashboard_stats_cache_reports_to_rollbar(
        self, mock_get_dashboard_stats, mock_rollbar
    ):
        """Test that errors are reported to rollbar."""
        # Setup mock to raise exception
        mock_get_dashboard_stats.side_effect = Exception("Test error")

        # Execute task
        refresh_dashboard_stats_cache.apply()

        # Verify rollbar was called for each error
        assert mock_rollbar.call_count == 8

    @patch("gefapi.tasks.stats_cache_refresh.StatsService.get_dashboard_stats")
    def test_refresh_dashboard_stats_cache_handles_app_context_error(
        self, mock_get_dashboard_stats
    ):
        """Test that application context errors are handled properly."""
        # Setup mock to raise application context error
        mock_get_dashboard_stats.side_effect = RuntimeError(
            "Working outside of application context"
        )

        # Execute task - should not crash and should handle error gracefully
        result = refresh_dashboard_stats_cache.apply().result

        # Verify error handling
        assert result["total_refreshed"] == 8  # All configs attempted
        assert result["successful"] == 0
        assert result["failed"] == 8
        assert len(result["cache_keys"]) == 0

    @patch("gefapi.services.stats_service.has_app_context")
    @patch("gefapi.services.stats_service.app")
    @patch("gefapi.tasks.stats_cache_refresh.StatsService._get_cache_key")
    def test_refresh_dashboard_stats_cache_creates_app_context_when_needed(
        self, mock_get_cache_key, mock_app, mock_has_app_context
    ):
        """Test that the service creates application context when not available."""
        # Setup mocks
        mock_get_cache_key.return_value = "test_cache_key"
        mock_has_app_context.return_value = False  # No app context available

        # Mock app context manager
        mock_app_context = MagicMock()
        mock_app.app_context.return_value = mock_app_context

        # Mock the database operations to avoid actual DB calls
        with patch("gefapi.services.stats_service.get_redis_cache") as mock_redis:
            mock_redis.return_value.is_available.return_value = False

            # Mock database queries to return valid data
            with patch("gefapi.services.stats_service.db.session") as mock_session:
                mock_session.query.return_value.scalar.return_value = 100

                # Execute task
                result = refresh_dashboard_stats_cache.apply().result

        # Verify app context was created when needed
        # Note: The exact number of calls depends on the service implementation
        # but we should see at least one call to create app context
        assert mock_app.app_context.call_count >= 1

        # Verify task completed successfully
        assert result["total_refreshed"] == 8


@pytest.mark.integration
class TestStatsTaskIntegration:
    """Integration tests for stats cache refresh task configuration."""

    def test_tasks_are_registered_with_celery(self, app):
        """Test that stats cache refresh tasks are registered with Celery."""
        from gefapi import celery

        # Check that our tasks are registered
        registered_tasks = celery.tasks.keys()

        expected_tasks = [
            "gefapi.tasks.stats_cache_refresh.refresh_dashboard_stats_cache",
            "gefapi.tasks.stats_cache_refresh.refresh_execution_stats_cache",
            "gefapi.tasks.stats_cache_refresh.refresh_user_stats_cache",
            "gefapi.tasks.stats_cache_refresh.warmup_stats_cache_on_startup",
        ]

        for task_name in expected_tasks:
            assert task_name in registered_tasks, f"Task {task_name} not registered"

    def test_beat_schedule_includes_stats_tasks(self, app):
        """Test that Celery beat schedule includes stats cache refresh tasks."""
        from gefapi import celery

        beat_schedule = celery.conf.beat_schedule

        # Check that our scheduled tasks are present
        expected_schedules = [
            "refresh-dashboard-stats-cache",
            "refresh-execution-stats-cache",
            "refresh-user-stats-cache",
        ]

        for schedule_name in expected_schedules:
            assert schedule_name in beat_schedule, f"Schedule {schedule_name} not found"
            assert "task" in beat_schedule[schedule_name], (
                f"Schedule {schedule_name} missing task"
            )
            assert "schedule" in beat_schedule[schedule_name], (
                f"Schedule {schedule_name} missing schedule"
            )

    def test_stats_tasks_use_correct_queue(self, app):
        """Test that stats tasks are routed to the correct queue."""
        from gefapi import celery

        task_routes = celery.conf.task_routes

        # Check task routing for stats refresh tasks
        expected_routes = [
            "gefapi.tasks.stats_cache_refresh.refresh_dashboard_stats_cache",
            "gefapi.tasks.stats_cache_refresh.refresh_execution_stats_cache",
            "gefapi.tasks.stats_cache_refresh.refresh_user_stats_cache",
            "gefapi.tasks.stats_cache_refresh.warmup_stats_cache_on_startup",
        ]

        for task_name in expected_routes:
            assert task_name in task_routes, f"Task {task_name} not routed"
            assert task_routes[task_name]["queue"] == "default", (
                f"Task {task_name} not using default queue"
            )
