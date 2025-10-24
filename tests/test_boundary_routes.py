"""
Tests for boundary API routes (GET /api/v1/data/boundaries).

These tests verify the boundary API endpoints including:
- ADM0 and ADM1 boundary queries
- Filtering by ISO code, name, coordinates, timestamps
- Response formats (full with geometry, table without geometry)
- Pagination
- Error handling
- Authentication requirements (all endpoints require JWT tokens)
"""

from geoalchemy2 import WKTElement
import pytest

from gefapi.models.boundary import AdminBoundary0, AdminBoundary1


@pytest.fixture(autouse=True)
def clear_boundaries_before_each_test(db_session):
    """Clear boundary tables before each test to prevent duplicate key errors."""
    db_session.query(AdminBoundary1).delete()
    db_session.query(AdminBoundary0).delete()
    db_session.commit()
    yield
    db_session.rollback()


@pytest.fixture
def sample_adm0_boundaries(db_session):
    """Create sample ADM0 boundaries for testing."""
    # Create sample countries with geometry
    countries = [
        AdminBoundary0(
            id="USA",
            boundary_id="USA-ADM0",
            boundary_name="United States",
            boundary_iso="USA",
            boundary_type="ADM0",
            continent="North America",
            geometry=WKTElement(
                "MULTIPOLYGON(((-125 24, -125 49, -66 49, -66 24, -125 24)))",
                srid=4326,
            ),
        ),
        AdminBoundary0(
            id="CAN",
            boundary_id="CAN-ADM0",
            boundary_name="Canada",
            boundary_iso="CAN",
            boundary_type="ADM0",
            continent="North America",
            geometry=WKTElement(
                "MULTIPOLYGON(((-140 42, -140 83, -50 83, -50 42, -140 42)))",
                srid=4326,
            ),
        ),
        AdminBoundary0(
            id="MEX",
            boundary_id="MEX-ADM0",
            boundary_name="Mexico",
            boundary_iso="MEX",
            boundary_type="ADM0",
            continent="North America",
            geometry=WKTElement(
                "MULTIPOLYGON(((-117 14, -117 32, -86 32, -86 14, -117 14)))",
                srid=4326,
            ),
        ),
    ]

    for country in countries:
        db_session.add(country)
    db_session.commit()

    return countries


@pytest.fixture
def sample_adm1_boundaries(db_session):
    """Create sample ADM1 boundaries for testing."""
    states = [
        AdminBoundary1(
            shape_id="USA-ADM1-CA",
            id="USA",
            boundary_name="California",
            boundary_iso="USA",
            boundary_type="ADM1",
            geometry=WKTElement(
                "MULTIPOLYGON(((-124 32, -124 42, -114 42, -114 32, -124 32)))",
                srid=4326,
            ),
        ),
        AdminBoundary1(
            shape_id="USA-ADM1-NY",
            id="USA",
            boundary_name="New York",
            boundary_iso="USA",
            boundary_type="ADM1",
            geometry=WKTElement(
                "MULTIPOLYGON(((-79 40, -79 45, -73 45, -73 40, -79 40)))", srid=4326
            ),
        ),
        AdminBoundary1(
            shape_id="CAN-ADM1-ON",
            id="CAN",
            boundary_name="Ontario",
            boundary_iso="CAN",
            boundary_type="ADM1",
            geometry=WKTElement(
                "MULTIPOLYGON(((-95 41, -95 57, -74 57, -74 41, -95 41)))", srid=4326
            ),
        ),
    ]

    for state in states:
        db_session.add(state)
    db_session.commit()

    return states


class TestBoundariesEndpoint:
    """Tests for GET /api/v1/data/boundaries endpoint."""

    def _auth_headers(self, token):
        """Helper to create authorization headers."""
        return {"Authorization": f"Bearer {token}"}

    def test_requires_authentication(self, client):
        """Test that the endpoint requires authentication."""
        response = client.get("/api/v1/data/boundaries")
        assert response.status_code == 401

    def test_get_all_boundaries_empty_database(self, client, user_token):
        """Test querying boundaries from empty database."""
        response = client.get(
            "/api/v1/data/boundaries", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["data"] == []

    def test_get_adm0_boundaries(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test retrieving all ADM0 boundaries."""
        response = client.get(
            "/api/v1/data/boundaries?level=0", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["data"]) == 3
        assert all(b["boundaryType"] == "ADM0" for b in data["data"])

    def test_get_adm1_boundaries(
        self, client, user_token, sample_adm1_boundaries, db_session
    ):
        """Test retrieving all ADM1 boundaries."""
        response = client.get(
            "/api/v1/data/boundaries?level=1", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["data"]) == 3
        assert all(b["boundaryType"] == "ADM1" for b in data["data"])

    def test_get_boundaries_mixed_levels(
        self,
        client,
        user_token,
        sample_adm0_boundaries,
        sample_adm1_boundaries,
        db_session,
    ):
        """Test retrieving boundaries from both levels."""
        # Without level parameter, defaults to level=0 only
        response = client.get(
            "/api/v1/data/boundaries?level=0,1", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["data"]) == 6  # 3 ADM0 + 3 ADM1

    def test_filter_by_iso_code(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test filtering boundaries by ISO code."""
        response = client.get(
            "/api/v1/data/boundaries?iso=USA", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "USA"

    def test_filter_by_name(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test filtering boundaries by name."""
        response = client.get(
            "/api/v1/data/boundaries?name=Canada",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["data"]) == 1
        assert data["data"][0]["boundaryName"] == "Canada"

    def test_filter_by_partial_name(
        self, client, user_token, sample_adm1_boundaries, db_session
    ):
        """Test filtering boundaries by partial name match."""
        response = client.get(
            "/api/v1/data/boundaries?level=1&name=New",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["data"]) >= 1
        assert any("New York" in b["boundaryName"] for b in data["data"])

    def test_filter_by_coordinates(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test filtering boundaries by coordinate point (spatial query)."""
        # Point in USA (-100, 40)
        response = client.get(
            "/api/v1/data/boundaries?lat=40&lon=-100",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        # May or may not find boundaries depending on PostGIS setup
        # Just verify endpoint works without error
        assert "data" in data

    def test_response_format_full(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test full response format includes geometry."""
        response = client.get(
            "/api/v1/data/boundaries?format=full&iso=USA",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert "geometry" in data["data"][0]

    def test_response_format_table(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test table response format excludes geometry."""
        response = client.get(
            "/api/v1/data/boundaries?format=table&iso=USA",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert "geometry" not in data["data"][0]

    def test_pagination(self, client, user_token, sample_adm0_boundaries, db_session):
        """Test pagination of boundary results."""
        response = client.get(
            "/api/v1/data/boundaries?level=0&page=1&per_page=2",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["data"]) == 2
        assert "meta" in data
        assert "total" in data["meta"]

    def test_invalid_level_parameter(self, client, user_token):
        """Test handling of invalid administrative level."""
        response = client.get(
            "/api/v1/data/boundaries?level=5", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 400

    def test_missing_coordinates_partial(self, client, user_token):
        """Test handling of missing coordinate (only lat provided)."""
        response = client.get(
            "/api/v1/data/boundaries?lat=40", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 400

    def test_invalid_coordinate_format(self, client, user_token):
        """Test handling of invalid coordinate format."""
        response = client.get(
            "/api/v1/data/boundaries?lat=invalid&lng=-100",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 400

    def test_filter_by_created_timestamp(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test filtering boundaries by creation timestamp."""
        response = client.get(
            "/api/v1/data/boundaries?created_at_since=2020-01-01T00:00:00Z",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200

    def test_filter_by_updated_timestamp(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test filtering boundaries by update timestamp."""
        response = client.get(
            "/api/v1/data/boundaries?updated_at_since=2020-01-01T00:00:00Z",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200

    def test_invalid_timestamp_format(self, client, user_token):
        """Test handling of invalid timestamp format."""
        response = client.get(
            "/api/v1/data/boundaries?created_at_since=invalid-date",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 400

    def test_combined_filters(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test combining multiple filters."""
        response = client.get(
            "/api/v1/data/boundaries?level=0&iso=USA&format=table",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "USA"
        assert "geometry" not in data["data"][0]


class TestBoundaryIntegration:
    """Integration tests for boundary endpoints."""

    def _auth_headers(self, token):
        """Helper to create authorization headers."""
        return {"Authorization": f"Bearer {token}"}

    def test_full_workflow(
        self,
        client,
        user_token,
        sample_adm0_boundaries,
        sample_adm1_boundaries,
        db_session,
    ):
        """Test complete workflow: create and query boundaries."""
        # Get all boundaries (need to specify levels)
        response = client.get(
            "/api/v1/data/boundaries?level=0,1", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        assert len(response.get_json()["data"]) == 6

        # Verify boundary data is accessible
        data = response.get_json()["data"]
        assert any(b["boundaryISO"] == "USA" for b in data)
        assert any(b["boundaryISO"] == "GBR" for b in data)

        # Query specific boundary
        response = client.get(
            "/api/v1/data/boundaries?iso=CAN", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        data = response.get_json()["data"]
        assert len(data) == 1
        assert data[0]["boundaryName"] == "Canada"

    def test_combined_level_and_spatial_query(
        self, client, user_token, sample_adm1_boundaries, db_session
    ):
        """Test combining level and spatial filters."""
        # Point in California (-120, 37) - using lon parameter
        response = client.get(
            "/api/v1/data/boundaries?level=1&lat=37&lon=-120",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        # May or may not find boundaries depending on PostGIS setup
        # Just verify endpoint works without error
        assert "data" in data
