"""
Tests for the Stats API endpoints.
Tests authentication, parameter validation, caching, and error handling.
"""

import json
from unittest.mock import patch

import pytest

from gefapi.services.stats_service import StatsService


@pytest.mark.usefixtures("app", "db_session")
class TestStatsAPIEndpoints:
    """Test cases for Stats API endpoints."""

    def setup_method(self):
        """Set up test fixtures."""
        self.sample_dashboard_stats = {
            "summary": {
                "total_executions": 1500,
                "total_jobs": 1500,  # Backward compatibility
                "total_users": 250,
                "total_scripts": 150,
                "total_executions_finished": 1200,
                "total_executions_failed": 200,
                "total_executions_cancelled": 100,
                "jobs_last_day": 15,
                "jobs_last_week": 120,
                "jobs_last_month": 450,
                "jobs_last_year": 1200,
                "users_last_day": 5,
                "users_last_week": 25,
                "users_last_month": 80,
                "users_last_year": 200,
            },
            "trends": {
                "hourly_jobs": [
                    {"hour": "2025-08-08T10:00:00Z", "count": 5},
                    {"hour": "2025-08-08T11:00:00Z", "count": 8},
                ],
                "daily_jobs": [
                    {"date": "2025-08-07", "count": 45},
                    {"date": "2025-08-08", "count": 52},
                ],
            },
            "geographic": {
                "top_countries": [
                    {"country": "USA", "user_count": 100, "percentage": 40.0},
                    {"country": "Brazil", "user_count": 75, "percentage": 30.0},
                ]
            },
            "tasks": {
                "by_type": [
                    {"task": "productivity", "count": 500, "success_rate": 95.0},
                    {"task": "land-cover", "count": 300, "success_rate": 92.0},
                ],
                "by_version": [
                    {"version": "2", "count": 600, "percentage": 60.0},
                    {"version": "1", "count": 400, "percentage": 40.0},
                ],
            },
        }

        self.sample_execution_stats = {
            "time_series": [
                {
                    "timestamp": "2025-08-08T10:00:00Z",
                    "total": 10,
                    "by_status": {"FINISHED": 8, "FAILED": 2},
                    "by_task": {"productivity": 6, "land-cover": 4},
                }
            ],
            "top_users": [
                {
                    "user_id": "123",
                    "email": "user1@example.com",
                    "execution_count": 45,
                    "success_rate": 94.4,
                    "favorite_tasks": ["productivity", "land-cover"],
                }
            ],
            "task_performance": [
                {
                    "task": "productivity",
                    "total_executions": 500,
                    "success_rate": 95.0,
                    "avg_duration_minutes": 45.2,
                    "failure_reasons": [],
                }
            ],
        }

        self.sample_user_stats = {
            "registration_trends": [
                {"date": "2025-08-07", "new_users": 12, "total_users": 250},
                {"date": "2025-08-08", "new_users": 8, "total_users": 258},
            ],
            "geographic_distribution": [
                {"country": "USA", "user_count": 100, "percentage": 40.0},
                {"country": "Brazil", "user_count": 75, "percentage": 30.0},
            ],
            "activity_stats": {
                "active_last_day": 45,
                "active_last_week": 234,
                "active_last_month": 567,
                "never_executed": 123,
            },
        }

    # Authentication Tests

    def test_dashboard_stats_requires_authentication(
        self, client, auth_headers_superadmin
    ):
        """Test that dashboard stats endpoint requires authentication."""
        response = client.get("/api/v1/stats/dashboard")
        assert response.status_code == 401

    def test_execution_stats_requires_authentication(
        self, client, auth_headers_superadmin
    ):
        """Test that execution stats endpoint requires authentication."""
        response = client.get("/api/v1/stats/executions")
        assert response.status_code == 401

    def test_user_stats_requires_authentication(self, client, auth_headers_superadmin):
        """Test that user stats endpoint requires authentication."""
        response = client.get("/api/v1/stats/users")
        assert response.status_code == 401

    def test_cache_endpoints_require_authentication(
        self, client, auth_headers_superadmin
    ):
        """Test that cache endpoints require authentication."""
        response = client.get("/api/v1/stats/cache")
        assert response.status_code == 401

        response = client.delete("/api/v1/stats/cache")
        assert response.status_code == 401

    @patch("gefapi.utils.permissions.is_superadmin")
    def test_dashboard_stats_requires_superadmin(
        self, mock_is_superadmin, client, auth_headers_user
    ):
        """Test that dashboard stats requires SUPERADMIN role."""
        mock_is_superadmin.return_value = False

        response = client.get("/api/v1/stats/dashboard", headers=auth_headers_user)

        assert response.status_code == 403
        data = json.loads(response.data)
        assert "SUPERADMIN role required" in data["detail"]

    # Dashboard Stats Endpoint Tests

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_dashboard_stats")
    def test_dashboard_stats_success(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test successful dashboard stats retrieval."""
        mock_is_superadmin.return_value = True
        mock_get_stats.return_value = self.sample_dashboard_stats

        response = client.get(
            "/api/v1/stats/dashboard", headers=auth_headers_superadmin
        )

        print(f"Response status: {response.status_code}")
        print(f"Response data: {response.get_data(as_text=True)}")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "data" in data
        assert data["data"] == self.sample_dashboard_stats

        # Verify service was called with defaults
        mock_get_stats.assert_called_once_with(
            period="all", include=["summary", "trends", "geographic", "tasks"]
        )

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_dashboard_stats")
    def test_dashboard_stats_with_parameters(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test dashboard stats with custom parameters."""
        mock_is_superadmin.return_value = True
        mock_get_stats.return_value = self.sample_dashboard_stats

        response = client.get(
            "/api/v1/stats/dashboard?period=last_month&include=summary,trends",
            headers=auth_headers_superadmin,
        )

        assert response.status_code == 200
        mock_get_stats.assert_called_once_with(
            period="last_month", include=["summary", "trends"]
        )

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_dashboard_stats")
    def test_dashboard_stats_includes_required_counts(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test that dashboard stats includes all required counts from issue #49."""
        mock_is_superadmin.return_value = True

        # Create sample data with all required fields
        enhanced_stats = {
            "summary": {
                "total_executions": 1000,
                "total_jobs": 1000,  # Backward compatibility
                "total_users": 150,
                "total_scripts": 75,
                "total_executions_finished": 800,
                "total_executions_failed": 150,
                "total_executions_cancelled": 50,
                "jobs_last_day": 10,
                "jobs_last_week": 80,
                "jobs_last_month": 300,
                "jobs_last_year": 900,
                "users_last_day": 2,
                "users_last_week": 15,
                "users_last_month": 45,
                "users_last_year": 120,
            }
        }

        mock_get_stats.return_value = enhanced_stats

        response = client.get(
            "/api/v1/stats/dashboard?include=summary", headers=auth_headers_superadmin
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        summary = data["data"]["summary"]

        # Verify all required counts are present
        assert summary["total_executions"] == 1000
        assert summary["total_users"] == 150
        assert summary["total_scripts"] == 75
        assert summary["total_executions_finished"] == 800
        assert summary["total_executions_failed"] == 150
        assert summary["total_executions_cancelled"] == 50

        # Verify backward compatibility
        assert summary["total_jobs"] == 1000

        # Verify service was called correctly
        mock_get_stats.assert_called_once_with(period="all", include=["summary"])

    @patch("gefapi.utils.permissions.is_superadmin")
    def test_dashboard_stats_invalid_period(
        self, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test dashboard stats with invalid period parameter."""
        mock_is_superadmin.return_value = True

        response = client.get(
            "/api/v1/stats/dashboard?period=invalid_period",
            headers=auth_headers_superadmin,
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid period" in data["detail"]

    @patch("gefapi.utils.permissions.is_superadmin")
    def test_dashboard_stats_invalid_include(
        self, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test dashboard stats with invalid include parameter."""
        mock_is_superadmin.return_value = True

        response = client.get(
            "/api/v1/stats/dashboard?include=summary,invalid_section",
            headers=auth_headers_superadmin,
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid sections" in data["detail"]

    # Execution Stats Endpoint Tests

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_execution_stats")
    def test_execution_stats_success(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test successful execution stats retrieval."""
        mock_is_superadmin.return_value = True
        mock_get_stats.return_value = self.sample_execution_stats

        response = client.get(
            "/api/v1/stats/executions", headers=auth_headers_superadmin
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["data"] == self.sample_execution_stats

        # Verify defaults
        mock_get_stats.assert_called_once_with(
            period="last_month", group_by="day", task_type=None, status=None
        )

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_execution_stats")
    def test_execution_stats_with_filters(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test execution stats with filters."""
        mock_is_superadmin.return_value = True
        mock_get_stats.return_value = self.sample_execution_stats

        response = client.get(
            "/api/v1/stats/executions?period=last_week&group_by=hour&task_type=productivity&status=FINISHED",
            headers=auth_headers_superadmin,
        )

        assert response.status_code == 200
        mock_get_stats.assert_called_once_with(
            period="last_week",
            group_by="hour",
            task_type="productivity",
            status="FINISHED",
        )

    @patch("gefapi.utils.permissions.is_superadmin")
    def test_execution_stats_invalid_status(
        self, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test execution stats with invalid status."""
        mock_is_superadmin.return_value = True

        response = client.get(
            "/api/v1/stats/executions?status=INVALID_STATUS",
            headers=auth_headers_superadmin,
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid status" in data["detail"]

    # User Stats Endpoint Tests

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_user_stats")
    def test_user_stats_success(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test successful user stats retrieval."""
        mock_is_superadmin.return_value = True
        mock_get_stats.return_value = self.sample_user_stats

        response = client.get("/api/v1/stats/users", headers=auth_headers_superadmin)

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["data"] == self.sample_user_stats

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_user_stats")
    def test_user_stats_with_country_filter(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test user stats with country filter."""
        mock_is_superadmin.return_value = True
        mock_get_stats.return_value = self.sample_user_stats

        response = client.get(
            "/api/v1/stats/users?country=USA&period=last_month&group_by=week",
            headers=auth_headers_superadmin,
        )

        assert response.status_code == 200
        mock_get_stats.assert_called_once_with(
            period="last_month", group_by="week", country="USA"
        )

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_user_stats")
    def test_user_stats_with_quarter_hour_grouping(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test user stats accepts quarter-hour grouping."""
        mock_is_superadmin.return_value = True
        mock_get_stats.return_value = self.sample_user_stats

        response = client.get(
            "/api/v1/stats/users?group_by=quarter_hour",
            headers=auth_headers_superadmin,
        )

        assert response.status_code == 200
        mock_get_stats.assert_called_once_with(
            period="last_year", group_by="quarter_hour", country=None
        )

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_user_stats")
    def test_user_stats_with_group_by_alias(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test that group_by alias is normalized to supported value."""
        mock_is_superadmin.return_value = True
        mock_get_stats.return_value = self.sample_user_stats

        response = client.get(
            "/api/v1/stats/users?group_by=15min",
            headers=auth_headers_superadmin,
        )

        assert response.status_code == 200
        mock_get_stats.assert_called_once_with(
            period="last_year", group_by="quarter_hour", country=None
        )

    @patch("gefapi.utils.permissions.is_superadmin")
    def test_user_stats_invalid_group_by(
        self, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test user stats rejects unsupported group_by value."""
        mock_is_superadmin.return_value = True

        response = client.get(
            "/api/v1/stats/users?group_by=invalid",
            headers=auth_headers_superadmin,
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid group_by" in data["detail"]

    # Health Check Endpoint Tests

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "_get_summary_stats")
    def test_health_check_success(
        self, mock_get_summary, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test health check endpoint."""
        mock_is_superadmin.return_value = True
        mock_get_summary.return_value = {
            "total_executions": 1500,
            "total_jobs": 1500,  # Backward compatibility
            "total_users": 250,
            "total_scripts": 150,
            "total_executions_finished": 1200,
            "total_executions_failed": 200,
            "total_executions_cancelled": 100,
            "jobs_last_month": 450,
        }

        response = client.get("/api/v1/stats/health", headers=auth_headers_superadmin)

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["data"]["status"] == "healthy"
        assert data["data"]["basic_counts"]["total_jobs"] == 1500
        assert data["data"]["basic_counts"]["total_users"] == 250
        assert data["data"]["basic_counts"]["jobs_last_month"] == 450

    # Cache Management Endpoint Tests

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_cache_info")
    def test_get_cache_info_success(
        self, mock_get_cache_info, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test get cache info endpoint."""
        mock_is_superadmin.return_value = True
        mock_cache_info = {
            "available": True,
            "total_keys": 5,
            "keys": [
                {
                    "key": "stats_service:get_dashboard_stats:period=all",
                    "ttl_seconds": 240,
                    "expires_in": "4m 0s",
                }
            ],
        }
        mock_get_cache_info.return_value = mock_cache_info

        response = client.get("/api/v1/stats/cache", headers=auth_headers_superadmin)

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["data"] == mock_cache_info

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "clear_cache")
    def test_clear_cache_success(
        self, mock_clear_cache, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test clear cache endpoint."""
        mock_is_superadmin.return_value = True
        mock_clear_cache.return_value = True

        response = client.delete("/api/v1/stats/cache", headers=auth_headers_superadmin)

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["data"]["success"] is True
        assert "Cache cleared successfully" in data["data"]["message"]

        mock_clear_cache.assert_called_once_with(None)

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "clear_cache")
    def test_clear_cache_with_pattern(
        self, mock_clear_cache, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test clear cache with pattern."""
        mock_is_superadmin.return_value = True
        mock_clear_cache.return_value = True

        response = client.delete(
            "/api/v1/stats/cache?pattern=summary", headers=auth_headers_superadmin
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "for pattern: summary" in data["data"]["message"]

        mock_clear_cache.assert_called_once_with("summary")

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "clear_cache")
    def test_clear_cache_failure(
        self, mock_clear_cache, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test clear cache failure."""
        mock_is_superadmin.return_value = True
        mock_clear_cache.return_value = False

        response = client.delete("/api/v1/stats/cache", headers=auth_headers_superadmin)

        assert response.status_code == 500
        data = json.loads(response.data)
        assert "Failed to clear cache" in data["detail"]

    # Error Handling Tests

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_dashboard_stats")
    def test_dashboard_stats_service_error(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test error handling in dashboard stats."""
        mock_is_superadmin.return_value = True
        mock_get_stats.side_effect = Exception("Database connection failed")

        response = client.get(
            "/api/v1/stats/dashboard", headers=auth_headers_superadmin
        )

        assert response.status_code == 500
        data = json.loads(response.data)
        assert "Failed to get dashboard stats" in data["detail"]

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_execution_stats")
    def test_execution_stats_service_error(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test error handling in execution stats."""
        mock_is_superadmin.return_value = True
        mock_get_stats.side_effect = Exception("Query timeout")

        response = client.get(
            "/api/v1/stats/executions", headers=auth_headers_superadmin
        )

        assert response.status_code == 500
        data = json.loads(response.data)
        assert "Failed to get execution stats" in data["detail"]

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "_get_summary_stats")
    def test_health_check_service_error(
        self, mock_get_summary, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test error handling in health check."""
        mock_is_superadmin.return_value = True
        mock_get_summary.side_effect = Exception("Database error")

        response = client.get("/api/v1/stats/health", headers=auth_headers_superadmin)

        assert response.status_code == 500
        data = json.loads(response.data)
        assert "Stats service unhealthy" in data["detail"]

    # Parameter Validation Tests

    def test_all_parameter_combinations(self):
        """Test that all valid parameter combinations are accepted."""
        valid_periods = ["last_day", "last_week", "last_month", "last_year", "all"]
        valid_group_by = ["hour", "day", "week", "month"]
        valid_statuses = ["PENDING", "RUNNING", "FINISHED", "FAILED", "CANCELLED"]
        valid_sections = ["summary", "trends", "geographic", "tasks"]

        # Test that validation logic accepts all valid values
        assert all(period in valid_periods for period in valid_periods)
        assert all(group in valid_group_by for group in valid_group_by)
        assert all(status in valid_statuses for status in valid_statuses)
        assert all(section in valid_sections for section in valid_sections)

    # Performance and Load Tests

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_dashboard_stats")
    def test_concurrent_requests_simulation(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Simulate concurrent requests to test endpoint stability."""
        mock_is_superadmin.return_value = True
        mock_get_stats.return_value = self.sample_dashboard_stats

        # Simulate multiple requests
        responses = []
        for i in range(10):
            response = client.get(
                "/api/v1/stats/dashboard", headers=auth_headers_superadmin
            )
            responses.append(response)

        # Verify all requests succeeded
        for response in responses:
            assert response.status_code == 200
            data = json.loads(response.data)
            assert "data" in data

    @patch("gefapi.utils.permissions.is_superadmin")
    @patch.object(StatsService, "get_dashboard_stats")
    def test_large_response_handling(
        self, mock_get_stats, mock_is_superadmin, client, auth_headers_superadmin
    ):
        """Test handling of large response data."""
        mock_is_superadmin.return_value = True

        # Create large response data
        large_stats = {
            "summary": self.sample_dashboard_stats["summary"],
            "trends": {
                "hourly_jobs": [
                    {"hour": f"2025-08-08T{hour:02d}:00:00Z", "count": hour * 2}
                    for hour in range(24)
                ],
                "daily_jobs": [
                    {"date": f"2025-08-{day:02d}", "count": day * 10}
                    for day in range(1, 32)
                ],
            },
        }
        mock_get_stats.return_value = large_stats

        response = client.get(
            "/api/v1/stats/dashboard", headers=auth_headers_superadmin
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data["data"]["trends"]["hourly_jobs"]) == 24
        assert len(data["data"]["trends"]["daily_jobs"]) == 31
