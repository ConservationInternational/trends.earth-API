"""
Testing utilities and helpers for Trends.Earth API tests
"""

from datetime import datetime, timedelta
import io
import tempfile
from typing import Any, Optional
from unittest.mock import MagicMock


class TestUtils:
    """Utility functions for testing"""

    @staticmethod
    def create_mock_file_data(
        filename: str = "test_script.py", content: str = "print('Hello World')"
    ) -> tuple:
        """Create mock file data for testing file uploads"""
        return (io.BytesIO(content.encode("utf-8")), filename)

    @staticmethod
    def create_temp_file(
        content: str = "print('Hello World')", suffix: str = ".py"
    ) -> str:
        """Create a temporary file and return its path"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as f:
            f.write(content)
            return f.name

    @staticmethod
    def get_auth_header(token: str) -> dict[str, str]:
        """Get authorization header for API requests"""
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def assert_response_structure(response_data: dict[str, Any], required_fields: list):
        """Assert that response has required structure"""
        for field in required_fields:
            assert field in response_data, f"Missing required field: {field}"

    @staticmethod
    def assert_pagination_structure(
        response_data: dict[str, Any], is_paginated: bool = True
    ):
        """Assert that response has appropriate structure based on pagination"""
        if is_paginated:
            TestUtils.assert_response_structure(
                response_data, ["data", "page", "per_page", "total"]
            )
        else:
            # Non-paginated responses only require data field
            TestUtils.assert_response_structure(response_data, ["data"])
            # Ensure pagination fields are NOT present
            pagination_fields = ["page", "per_page", "total"]
            for field in pagination_fields:
                assert field not in response_data, (
                    f"Unexpected pagination field in non-paginated response: {field}"
                )

    @staticmethod
    def create_mock_celery_task():
        """Create a mock Celery task for testing"""
        mock_task = MagicMock()
        mock_task.delay.return_value.id = "test-task-id"
        mock_task.delay.return_value.status = "PENDING"
        return mock_task


class DateTestUtils:
    """Date-related testing utilities"""

    @staticmethod
    def get_iso_string(days_offset: int = 0) -> str:
        """Get ISO format date string with optional offset"""
        date = datetime.utcnow() + timedelta(days=days_offset)
        return date.isoformat()

    @staticmethod
    def get_date_range(days_back: int = 7) -> tuple:
        """Get start and end dates for testing date ranges"""
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days_back)
        return start_date.isoformat(), end_date.isoformat()


class StatusTestUtils:
    """Testing utilities for status endpoints"""

    @staticmethod
    def create_sample_status_data() -> dict[str, Any]:
        """Create sample status data for testing"""
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "executions_pending": 2,
            "executions_completed_today": 10,
            "memory_usage_percent": 45.2,
            "disk_usage_percent": 30.8,
            "database_connections": 8,
            "redis_connections": 3,
            "celery_active_tasks": 5,
        }


class ErrorTestUtils:
    """Testing utilities for error scenarios"""

    @staticmethod
    def assert_error_response(
        response, expected_status: int, expected_detail: Optional[str] = None
    ):
        """Assert that response is an error with expected format"""
        assert response.status_code == expected_status
        data = response.json
        assert "error" in data
        if expected_detail:
            assert expected_detail in data["error"]["detail"]

    @staticmethod
    def assert_validation_error(response, field_name: Optional[str] = None):
        """Assert that response is a validation error"""
        ErrorTestUtils.assert_error_response(response, 400)
        if field_name:
            assert field_name in response.json["error"]["detail"]


class DatabaseTestUtils:
    """Database testing utilities"""

    @staticmethod
    def count_records(model_class):
        """Count records in a model table"""
        return model_class.query.count()

    @staticmethod
    def get_last_record(model_class):
        """Get the last created record from a model"""
        return model_class.query.order_by(model_class.id.desc()).first()

    @staticmethod
    def clear_table(model_class, db_session):
        """Clear all records from a model table"""
        db_session.query(model_class).delete()
        db_session.commit()


class MockServices:
    """Mock services for testing"""

    @staticmethod
    def mock_docker_service():
        """Create mock Docker service"""
        mock_service = MagicMock()
        mock_service.run_script.return_value = {
            "container_id": "test-container-123",
            "status": "running",
        }
        return mock_service

    @staticmethod
    def mock_email_service():
        """Create mock Email service"""
        mock_service = MagicMock()
        mock_service.send_email.return_value = True
        return mock_service

    @staticmethod
    def mock_s3_service():
        """Create mock S3 service"""
        mock_service = MagicMock()
        mock_service.upload_file.return_value = "https://s3.example.com/test-file"
        mock_service.download_file.return_value = "file content"
        return mock_service


# Common test data constants
TEST_USER_DATA = {
    "email": "test@example.com",
    "password": "testpassword123",
    "name": "Test User",
    "role": "USER",
}

TEST_ADMIN_DATA = {
    "email": "admin@example.com",
    "password": "adminpassword123",
    "name": "Admin User",
    "role": "ADMIN",
}

TEST_SCRIPT_CONTENT = """
import sys
import json

def main():
    print("Hello from test script!")
    result = {
        'status': 'success',
        'message': 'Test execution completed',
        'data': {'value': 42}
    }
    print(json.dumps(result))

if __name__ == '__main__':
    main()
"""

SAMPLE_EXECUTION_LOG = """
2025-01-14 10:00:00 - Starting script execution
2025-01-14 10:00:01 - Loading dependencies
2025-01-14 10:00:05 - Processing data
2025-01-14 10:00:10 - Generating results
2025-01-14 10:00:15 - Script execution completed successfully
"""
