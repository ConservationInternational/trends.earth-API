"""
Test pagination behavior for executions endpoint
"""

from tests.test_utils import TestUtils


class TestExecutionPagination:
    """Test execution endpoint pagination behavior"""

    def test_executions_without_pagination_params(self, client, auth_headers_user):
        """Test that executions endpoint returns all results without pagination metadata when no pagination params are provided"""
        response = client.get("/api/v1/execution", headers=auth_headers_user)

        assert response.status_code == 200
        data = response.json

        # Should have data field
        assert "data" in data
        assert isinstance(data["data"], list)

        # Should NOT have pagination metadata
        pagination_fields = ["page", "per_page", "total"]
        for field in pagination_fields:
            assert field not in data, (
                f"Unexpected pagination field '{field}' in non-paginated response"
            )

    def test_executions_with_page_param(self, client, auth_headers_user):
        """Test that executions endpoint returns paginated results when page param is provided"""
        response = client.get("/api/v1/execution?page=1", headers=auth_headers_user)

        assert response.status_code == 200
        data = response.json

        # Should have data field and pagination metadata
        TestUtils.assert_pagination_structure(data, is_paginated=True)

        # Check pagination metadata values
        assert data["page"] == 1
        assert data["per_page"] == 20  # Default value
        assert isinstance(data["total"], int)

    def test_executions_with_per_page_param(self, client, auth_headers_user):
        """Test that executions endpoint returns paginated results when per_page param is provided"""
        response = client.get(
            "/api/v1/execution?per_page=10", headers=auth_headers_user
        )

        assert response.status_code == 200
        data = response.json

        # Should have data field and pagination metadata
        TestUtils.assert_pagination_structure(data, is_paginated=True)

        # Check pagination metadata values
        assert data["page"] == 1  # Default value
        assert data["per_page"] == 10
        assert isinstance(data["total"], int)

    def test_executions_with_both_pagination_params(self, client, auth_headers_user):
        """Test that executions endpoint returns paginated results when both page and per_page params are provided"""
        response = client.get(
            "/api/v1/execution?page=2&per_page=5", headers=auth_headers_user
        )

        assert response.status_code == 200
        data = response.json

        # Should have data field and pagination metadata
        TestUtils.assert_pagination_structure(data, is_paginated=True)

        # Check pagination metadata values
        assert data["page"] == 2
        assert data["per_page"] == 5
        assert isinstance(data["total"], int)

    def test_executions_with_filters_no_pagination(self, client, auth_headers_user):
        """Test that filters work without pagination parameters"""
        response = client.get(
            "/api/v1/execution?status=FINISHED", headers=auth_headers_user
        )

        assert response.status_code == 200
        data = response.json

        # Should have data field but no pagination metadata
        TestUtils.assert_pagination_structure(data, is_paginated=False)

    def test_executions_with_filters_and_pagination(self, client, auth_headers_user):
        """Test that filters work with pagination parameters"""
        response = client.get(
            "/api/v1/execution?status=FINISHED&page=1&per_page=10",
            headers=auth_headers_user,
        )

        assert response.status_code == 200
        data = response.json

        # Should have data field and pagination metadata
        TestUtils.assert_pagination_structure(data, is_paginated=True)
        assert data["page"] == 1
        assert data["per_page"] == 10

    def test_executions_with_sorting_no_pagination(self, client, auth_headers_user):
        """Test that sorting works without pagination parameters"""
        response = client.get(
            "/api/v1/execution?sort=-start_date", headers=auth_headers_user
        )

        assert response.status_code == 200
        data = response.json

        # Should have data field but no pagination metadata
        TestUtils.assert_pagination_structure(data, is_paginated=False)

    def test_executions_with_include_exclude_no_pagination(
        self, client, auth_headers_user
    ):
        """Test that include/exclude parameters work without pagination"""
        response = client.get(
            "/api/v1/execution?include=duration&exclude=params",
            headers=auth_headers_user,
        )

        assert response.status_code == 200
        data = response.json

        # Should have data field but no pagination metadata
        TestUtils.assert_pagination_structure(data, is_paginated=False)

    def test_pagination_parameter_validation(self, client, auth_headers_user):
        """Test that pagination parameters are properly validated"""
        # Test with invalid page (should still work, defaulting to valid values)
        response = client.get("/api/v1/execution?page=0", headers=auth_headers_user)
        assert response.status_code == 200
        data = response.json
        assert data["page"] == 1  # Should be corrected to minimum value

        # Test with per_page over limit
        response = client.get(
            "/api/v1/execution?per_page=200", headers=auth_headers_user
        )
        assert response.status_code == 200
        data = response.json
        assert data["per_page"] == 100  # Should be capped at maximum value
