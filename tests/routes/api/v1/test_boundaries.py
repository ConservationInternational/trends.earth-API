"""
Tests for the Boundaries API endpoints.
Tests parameter validation, query functionality, response formats, and error handling.
"""

import json
from unittest.mock import patch

import pytest

from gefapi.services.boundaries_service import BoundariesService


@pytest.mark.usefixtures("app", "db_session")
class TestBoundariesAPIEndpoints:
    """Test cases for Boundaries API endpoints."""

    def setup_method(self):
        """Set up test fixtures."""
        # Sample ADM0 (country) data
        self.sample_country = {
            "id": "USA",
            "shape_name": "United States",
            "shape_group": "Country",
            "shape_type": "ADM0",
        }

        # Sample ADM1 (state) data
        self.sample_state = {
            "shape_id": "USA-ADM1-12345",
            "id": "USA",
            "shape_name": "California",
            "shape_group": "State",
            "shape_type": "ADM1",
        }

        # Expected service responses
        self.expected_countries = [
            {
                "id": "USA",
                "shape_name": "United States",
                "shape_group": "Country",
                "shape_type": "ADM0",
                "created_at": "2025-01-08T12:00:00Z",
                "updated_at": "2025-01-08T12:00:00Z",
            }
        ]

        self.expected_states = [
            {
                "shape_id": "USA-ADM1-12345",
                "id": "USA",
                "shape_name": "California",
                "shape_group": "State",
                "shape_type": "ADM1",
                "created_at": "2025-01-08T12:00:00Z",
                "updated_at": "2025-01-08T12:00:00Z",
            }
        ]

    def test_get_boundaries_level_0_success(self, client):
        """Test successful retrieval of ADM0 boundaries."""
        with patch.object(BoundariesService, "get_boundaries") as mock_service:
            mock_service.return_value = (self.expected_countries, 1)

            response = client.get("/api/v1/data/boundaries?level=0&format=table")

            assert response.status_code == 200
            data = json.loads(response.data)
            assert "data" in data
            assert "meta" in data
            assert data["meta"]["levels"] == [0]
            assert data["meta"]["format"] == "table"
            assert data["meta"]["total"] == 1
            mock_service.assert_called_once()

    def test_get_boundaries_level_1_success(self, client):
        """Test successful retrieval of ADM1 boundaries."""
        with patch.object(BoundariesService, "get_boundaries") as mock_service:
            mock_service.return_value = (self.expected_states, 1)

            response = client.get("/api/v1/data/boundaries?level=1&iso=USA")

            assert response.status_code == 200
            data = json.loads(response.data)
            assert "data" in data
            assert "meta" in data
            assert data["meta"]["levels"] == [1]
            assert data["meta"]["filter_iso"] == "USA"
            mock_service.assert_called_once()

    def test_get_boundaries_invalid_level(self, client):
        """Test error handling for invalid administrative level."""
        response = client.get("/api/v1/data/boundaries?level=2")

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid level parameter" in data["detail"]

    def test_get_boundaries_invalid_format(self, client):
        """Test error handling for invalid response format."""
        response = client.get("/api/v1/data/boundaries?format=invalid")

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid format parameter" in data["detail"]

    def test_get_boundaries_invalid_per_page(self, client):
        """Test error handling for invalid per_page parameter."""
        response = client.get("/api/v1/data/boundaries?per_page=abc")

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid page or per_page parameter" in data["detail"]

    def test_get_boundaries_coordinates_validation(self, client):
        """Test coordinate parameter validation."""
        # Missing longitude
        response = client.get("/api/v1/data/boundaries?lat=40.7128")
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Both lat and lon parameters are required" in data["detail"]

        # Invalid latitude
        response = client.get("/api/v1/data/boundaries?lat=100&lon=-74")
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid coordinate values" in data["detail"]

        # Invalid coordinate format
        response = client.get("/api/v1/data/boundaries?lat=abc&lon=def")
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid coordinate format" in data["detail"]

    def test_get_boundaries_point_query(self, client):
        """Test point-in-polygon query functionality."""
        with patch.object(BoundariesService, "get_boundaries") as mock_service:
            mock_service.return_value = (self.expected_countries, 1)

            response = client.get(
                "/api/v1/data/boundaries?lat=40.7128&lon=-74.0060&level=0"
            )

            assert response.status_code == 200
            data = json.loads(response.data)
            assert "filter_point" in data["meta"]
            assert data["meta"]["filter_point"]["lat"] == 40.7128
            assert data["meta"]["filter_point"]["lon"] == -74.0060

            # Verify service was called with correct filters
            call_args = mock_service.call_args
            assert "lat" in call_args[1]["filters"]
            assert "lon" in call_args[1]["filters"]

    def test_get_boundaries_name_filter(self, client):
        """Test name filtering functionality."""
        with patch.object(BoundariesService, "get_boundaries") as mock_service:
            mock_service.return_value = (self.expected_states, 1)

            response = client.get("/api/v1/data/boundaries?level=1&name=california")

            assert response.status_code == 200
            data = json.loads(response.data)
            assert "filter_name" in data["meta"]
            assert data["meta"]["filter_name"] == "california"

    def test_get_boundaries_no_results(self, client):
        """Test handling when no boundaries are found."""
        with patch.object(BoundariesService, "get_boundaries") as mock_service:
            mock_service.return_value = ([], 0)

            response = client.get("/api/v1/data/boundaries?iso=INVALID")

            assert response.status_code == 404
            data = json.loads(response.data)
            assert "No boundaries found" in data["detail"]

    def test_get_boundaries_pagination(self, client):
        """Test pagination functionality."""
        with patch.object(BoundariesService, "get_boundaries") as mock_service:
            mock_service.return_value = (self.expected_countries, 100)

            response = client.get("/api/v1/data/boundaries?page=3&per_page=10")

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["meta"]["per_page"] == 10
            assert data["meta"]["page"] == 3
            assert data["meta"]["total"] == 100
            assert data["meta"]["has_more"] is True

    def test_get_boundary_statistics_success(self, client):
        """Test successful boundary statistics endpoint."""
        expected_stats = {
            "admin_level_0": {"total_boundaries": 218},
            "admin_level_1": {"total_boundaries": 3224, "countries_with_admin1": 195},
            "total_boundaries": 3442,
        }

        with patch.object(BoundariesService, "get_boundary_statistics") as mock_service:
            mock_service.return_value = expected_stats

            response = client.get("/api/v1/data/boundaries/stats")

            assert response.status_code == 200
            data = json.loads(response.data)
            assert "data" in data
            assert data["data"]["admin_level_0"]["total_boundaries"] == 218
            assert data["data"]["admin_level_1"]["total_boundaries"] == 3224
            assert data["data"]["total_boundaries"] == 3442
            mock_service.assert_called_once()

    def test_boundaries_service_exception_handling(self, client):
        """Test API error handling when service raises exceptions."""
        with patch.object(BoundariesService, "get_boundaries") as mock_service:
            mock_service.side_effect = Exception("Database connection error")

            response = client.get("/api/v1/data/boundaries")

            assert response.status_code == 500
            data = json.loads(response.data)
            assert "Internal server error" in data["detail"]

    def test_stats_service_exception_handling(self, client):
        """Test statistics API error handling when service raises exceptions."""
        with patch.object(BoundariesService, "get_boundary_statistics") as mock_service:
            mock_service.side_effect = Exception("Database connection error")

            response = client.get("/api/v1/data/boundaries/stats")

            assert response.status_code == 500
            data = json.loads(response.data)
            assert "Internal server error" in data["detail"]


@pytest.mark.usefixtures("app", "db_session")
class TestBoundariesServiceIntegration:
    """Integration tests for Boundaries Service with actual database operations."""

    def test_validate_point_coordinates(self):
        """Test coordinate validation logic."""
        # Valid coordinates
        assert BoundariesService.validate_point_coordinates(40.7128, -74.0060) is True
        assert BoundariesService.validate_point_coordinates(0, 0) is True
        assert BoundariesService.validate_point_coordinates(-90, -180) is True
        assert BoundariesService.validate_point_coordinates(90, 180) is True

        # Invalid coordinates
        assert BoundariesService.validate_point_coordinates(91, 0) is False
        assert BoundariesService.validate_point_coordinates(-91, 0) is False
        assert BoundariesService.validate_point_coordinates(0, 181) is False
        assert BoundariesService.validate_point_coordinates(0, -181) is False

    def test_get_boundary_statistics_empty_database(self):
        """Test statistics with empty database."""
        stats = BoundariesService.get_boundary_statistics()

        assert "total_countries" in stats
        assert "total_admin1_units" in stats
        assert "levels_available" in stats

    @pytest.mark.slow
    def test_boundaries_endpoint_response_format(self, client):
        """Test that response formats are properly structured."""
        # Test with mocked data to ensure proper format structure
        with patch.object(BoundariesService, "get_boundaries") as mock_service:
            mock_service.return_value = (
                [
                    {
                        "id": "USA",
                        "shape_name": "United States",
                        "shape_group": "Country",
                        "shape_type": "ADM0",
                        "created_at": "2025-01-08T12:00:00Z",
                        "updated_at": "2025-01-08T12:00:00Z",
                    }
                ],
                1,
            )

            # Test table format (no geometry)
            response = client.get("/api/v1/data/boundaries?format=table")
            assert response.status_code == 200
            data = json.loads(response.data)

            # Verify response structure
            assert "data" in data
            assert "meta" in data
            assert isinstance(data["data"], list)
            assert len(data["data"]) == 1

            # Verify metadata structure
            meta = data["meta"]
            required_meta_fields = [
                "levels",
                "format",
                "total",
                "per_page",
                "page",
                "has_more",
            ]
            for field in required_meta_fields:
                assert field in meta

            assert meta["format"] == "table"
            assert meta["total"] == 1
            assert isinstance(meta["has_more"], bool)

    def test_boundaries_endpoint_full_format_with_geometry(self, client):
        """Test that format=full includes geometry in the response."""
        # Sample geometry data (mocked GeoJSON)
        sample_geometry = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[-180, -90], [-180, 90], [180, 90], [180, -90], [-180, -90]]]
            ],
        }

        with patch.object(BoundariesService, "get_boundaries") as mock_service:
            # Mock boundary with geometry
            mock_service.return_value = (
                [
                    {
                        "id": "USA",
                        "shape_name": "United States",
                        "shape_group": "Country",
                        "shape_type": "ADM0",
                        "created_at": "2025-01-08T12:00:00Z",
                        "updated_at": "2025-01-08T12:00:00Z",
                        "geometry": sample_geometry,  # Include geometry in mock
                    }
                ],
                1,
            )

            # Test full format (should include geometry)
            response = client.get("/api/v1/data/boundaries?format=full")
            assert response.status_code == 200
            data = json.loads(response.data)

            # Verify response structure
            assert "data" in data
            assert "meta" in data
            assert len(data["data"]) == 1

            # Verify metadata indicates full format
            meta = data["meta"]
            assert meta["format"] == "full"

            # Verify geometry is included in the boundary data
            boundary = data["data"][0]
            assert "geometry" in boundary
            assert boundary["geometry"] is not None
            assert boundary["geometry"]["type"] == "MultiPolygon"
            assert "coordinates" in boundary["geometry"]

            # Verify other expected fields are still present
            assert "id" in boundary
            assert "shape_name" in boundary
            assert "shape_type" in boundary
            assert boundary["id"] == "USA"
            assert boundary["shape_name"] == "United States"
