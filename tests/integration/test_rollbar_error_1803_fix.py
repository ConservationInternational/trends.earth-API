"""
Edge case test for the specific rollbar error scenario.

This test simulates the exact call chain that was failing:
refresh_dashboard_stats_cache -> StatsService.get_dashboard_stats ->
_get_from_cache_or_execute -> execute_stats -> _get_summary_stats -> db.session.query
"""

from unittest.mock import MagicMock, Mock, patch

import pytest


class TestRollbarError1803Fix:
    """Test for the specific rollbar error 1803 scenario."""

    def test_celery_task_db_access_without_app_context(self):
        """
        Test the exact scenario from rollbar error 1803.

        This simulates:
        1. Celery task calls StatsService.get_dashboard_stats()
        2. Service calls _get_from_cache_or_execute with execute_stats function
        3. execute_stats calls _get_summary_stats which accesses db.session
        4. Without the fix, this would fail with "Working outside of application context"
        """

        # Mock the Flask app and database
        with (
            patch("gefapi.services.stats_service.app") as mock_app,
            patch("gefapi.services.stats_service.db") as mock_db,
            patch("gefapi.services.stats_service.get_redis_cache") as mock_redis,
        ):
            # Setup Redis to force execution (cache miss)
            mock_redis_instance = Mock()
            mock_redis_instance.is_available.return_value = True
            mock_redis_instance.get.return_value = None  # Cache miss
            mock_redis_instance.set.return_value = True
            mock_redis.return_value = mock_redis_instance

            # Mock has_app_context to return False initially (no context)
            with patch(
                "gefapi.services.stats_service.has_app_context",
                return_value=False,
            ):
                # Setup app context manager
                mock_app_context = MagicMock()
                mock_app.app_context.return_value = mock_app_context

                # Mock database session and query that was failing
                mock_session = Mock()
                mock_query = Mock()
                mock_query.scalar.return_value = 100  # Mock count result
                mock_session.query.return_value = mock_query
                mock_db.session = mock_session

                # Import the service after mocking
                from gefapi.services.stats_service import StatsService

                # Call the exact method that was failing
                try:
                    result = StatsService._get_summary_stats("all")

                    # Verify the fix worked
                    assert result is not None
                    assert isinstance(result, dict)

                    # Verify app context was created
                    mock_app.app_context.assert_called_once()
                    mock_app_context.__enter__.assert_called_once()
                    mock_app_context.__exit__.assert_called_once()

                    # Verify database was accessed within the context
                    mock_session.query.assert_called()

                    print("✅ Fix successfully handles the rollbar error 1803 scenario")

                except RuntimeError as e:
                    if "Working outside of application context" in str(e):
                        pytest.fail(
                            "❌ Fix did not work - still getting app context error"
                        )
                    else:
                        pytest.fail(f"❌ Unexpected RuntimeError: {e}")

    def test_dashboard_stats_cache_refresh_scenario(self):
        """
        Test the complete call chain from the failing Celery task.

        This simulates the exact call from refresh_dashboard_stats_cache task:
        refresh_dashboard_stats_cache -> StatsService.get_dashboard_stats(period="all", include=["summary"])
        """

        with (
            patch("gefapi.services.stats_service.app") as mock_app,
            patch("gefapi.services.stats_service.db") as mock_db,
            patch("gefapi.services.stats_service.get_redis_cache") as mock_redis,
        ):
            # Setup all mocks like in the previous test
            mock_redis_instance = Mock()
            mock_redis_instance.is_available.return_value = True
            mock_redis_instance.get.return_value = None  # Force execution
            mock_redis_instance.set.return_value = True
            mock_redis.return_value = mock_redis_instance

            with patch(
                "gefapi.services.stats_service.has_app_context",
                return_value=False,
            ):
                mock_app_context = MagicMock()
                mock_app.app_context.return_value = mock_app_context

                # Mock all database operations that _get_summary_stats performs
                mock_session = Mock()

                # Mock the queries from _get_summary_stats
                mock_execution_query = Mock()
                mock_execution_query.scalar.return_value = 150  # total_executions
                mock_execution_query.filter.return_value = mock_execution_query

                mock_user_query = Mock()
                mock_user_query.scalar.return_value = 25  # total_users
                mock_user_query.filter.return_value = mock_user_query

                mock_script_query = Mock()
                mock_script_query.scalar.return_value = 10  # total_scripts

                # Setup query method to return appropriate mocks
                def mock_query_side_effect(*args):
                    if "Execution" in str(args):
                        return mock_execution_query
                    elif "User" in str(args):
                        return mock_user_query
                    elif "Script" in str(args):
                        return mock_script_query
                    return Mock()

                mock_session.query.side_effect = mock_query_side_effect
                mock_db.session = mock_session

                # Import and test the service
                from gefapi.services.stats_service import StatsService

                try:
                    # This is the exact call that was failing in the Celery task
                    result = StatsService.get_dashboard_stats(
                        period="all", include=["summary"]
                    )

                    # Verify the result structure
                    assert "summary" in result
                    assert isinstance(result["summary"], dict)

                    # Verify app context was created multiple times as needed
                    assert mock_app.app_context.call_count >= 1

                    print("✅ Complete dashboard stats call chain works correctly")

                except RuntimeError as e:
                    if "Working outside of application context" in str(e):
                        pytest.fail("❌ Fix incomplete - dashboard stats still fails")
                    else:
                        pytest.fail(f"❌ Unexpected error in dashboard stats: {e}")


if __name__ == "__main__":
    # Run the tests directly
    import sys

    test_instance = TestRollbarError1803Fix()

    try:
        print("Testing specific rollbar error 1803 scenario...")
        test_instance.test_celery_task_db_access_without_app_context()

        print("\nTesting complete dashboard stats call chain...")
        test_instance.test_dashboard_stats_cache_refresh_scenario()

        print("\n🎉 All rollbar error 1803 tests passed!")
        print("✅ The fix should resolve the reported issue")

    except Exception as e:
        print(f"\n💥 Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
