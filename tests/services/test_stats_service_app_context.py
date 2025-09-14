"""
Test for the application context fix in stats service.

This test ensures that the StatsService._get_from_cache_or_execute method
properly handles database operations both with and without Flask application context.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from flask import Flask

from gefapi.services.stats_service import StatsService


class TestStatsServiceAppContextFix:
    """Test suite for application context handling in StatsService."""

    @patch('gefapi.services.stats_service.get_redis_cache')
    @patch('gefapi.services.stats_service.has_app_context')
    def test_get_from_cache_or_execute_with_app_context(self, mock_has_app_context, mock_get_redis_cache):
        """Test that execution works when app context is already available."""
        # Setup mocks
        mock_redis = Mock()
        mock_redis.is_available.return_value = True
        mock_redis.get.return_value = None  # Cache miss
        mock_redis.set.return_value = True
        mock_get_redis_cache.return_value = mock_redis
        
        mock_has_app_context.return_value = True  # We have app context
        
        # Mock execution function
        mock_execution_func = Mock(return_value={"test": "data"})
        
        # Execute
        result = StatsService._get_from_cache_or_execute("test_key", mock_execution_func)
        
        # Verify
        assert result == {"test": "data"}
        mock_execution_func.assert_called_once()
        mock_has_app_context.assert_called_once()
        # Should not try to create app context since we already have one

    @patch('gefapi.services.stats_service.get_redis_cache')
    @patch('gefapi.services.stats_service.has_app_context')
    @patch('gefapi.services.stats_service.app')
    def test_get_from_cache_or_execute_without_app_context(self, mock_app, mock_has_app_context, mock_get_redis_cache):
        """Test that execution works when no app context is available."""
        # Setup mocks
        mock_redis = Mock()
        mock_redis.is_available.return_value = True
        mock_redis.get.return_value = None  # Cache miss
        mock_redis.set.return_value = True
        mock_get_redis_cache.return_value = mock_redis
        
        mock_has_app_context.return_value = False  # No app context
        
        # Mock app context manager
        mock_app_context = MagicMock()
        mock_app.app_context.return_value = mock_app_context
        
        # Mock execution function
        mock_execution_func = Mock(return_value={"test": "data"})
        
        # Execute
        result = StatsService._get_from_cache_or_execute("test_key", mock_execution_func)
        
        # Verify
        assert result == {"test": "data"}
        mock_execution_func.assert_called_once()
        mock_has_app_context.assert_called_once()
        # Should have created app context
        mock_app.app_context.assert_called_once()
        mock_app_context.__enter__.assert_called_once()
        mock_app_context.__exit__.assert_called_once()

    @patch('gefapi.services.stats_service.get_redis_cache')
    @patch('gefapi.services.stats_service.has_app_context')
    @patch('gefapi.services.stats_service.app')
    def test_get_from_cache_or_execute_handles_exception_in_context(self, mock_app, mock_has_app_context, mock_get_redis_cache):
        """Test that exceptions are properly propagated when using app context."""
        # Setup mocks
        mock_redis = Mock()
        mock_redis.is_available.return_value = False  # No Redis
        mock_get_redis_cache.return_value = mock_redis
        
        mock_has_app_context.return_value = False  # No app context
        
        # Mock app context manager
        mock_app_context = MagicMock()
        mock_app.app_context.return_value = mock_app_context
        
        # Mock execution function that raises exception
        test_exception = RuntimeError("Database error")
        mock_execution_func = Mock(side_effect=test_exception)
        
        # Execute and verify exception is raised
        with pytest.raises(RuntimeError, match="Database error"):
            StatsService._get_from_cache_or_execute("test_key", mock_execution_func)
        
        # Verify app context was created and cleaned up
        mock_app.app_context.assert_called_once()
        mock_app_context.__enter__.assert_called_once()
        mock_app_context.__exit__.assert_called_once()

    @patch('gefapi.services.stats_service.get_redis_cache')  
    def test_get_from_cache_or_execute_cache_hit(self, mock_get_redis_cache):
        """Test that cached data is returned without executing function."""
        # Setup mocks
        mock_redis = Mock()
        mock_redis.is_available.return_value = True
        cached_data = {"cached": "result"}
        mock_redis.get.return_value = cached_data
        mock_get_redis_cache.return_value = mock_redis
        
        # Mock execution function (should not be called)
        mock_execution_func = Mock()
        
        # Execute
        result = StatsService._get_from_cache_or_execute("test_key", mock_execution_func)
        
        # Verify
        assert result == cached_data
        mock_execution_func.assert_not_called()  # Should not execute when cache hits

    @patch('gefapi.services.stats_service.logger')
    @patch('gefapi.services.stats_service.get_redis_cache')
    @patch('gefapi.services.stats_service.has_app_context')
    @patch('gefapi.services.stats_service.app')
    def test_get_from_cache_or_execute_logs_errors_correctly(self, mock_app, mock_has_app_context, mock_get_redis_cache, mock_logger):
        """Test that errors are logged with proper context information."""
        # Setup mocks
        mock_redis = Mock()
        mock_redis.is_available.return_value = False
        mock_get_redis_cache.return_value = mock_redis
        
        mock_has_app_context.return_value = False
        
        mock_app_context = MagicMock()
        mock_app.app_context.return_value = mock_app_context
        
        # Mock execution function that raises exception
        test_exception = RuntimeError("Test database error")
        mock_execution_func = Mock(side_effect=test_exception)
        
        # Execute and catch exception
        with pytest.raises(RuntimeError):
            StatsService._get_from_cache_or_execute("test_cache_key", mock_execution_func)
        
        # Verify error was logged with cache key context
        mock_logger.error.assert_called_once()
        logged_message = mock_logger.error.call_args[0][0]
        assert "test_cache_key" in logged_message
        assert "Error executing stats function" in logged_message