"""
Tests for boundary API routes (GET /api/v1/data/boundaries).

These tests verify the boundary API endpoints including:
- ADM0 and ADM1 boundary queries
- Filtering by ISO code, name, release types, timestamps
- Response formats with metadata and download URLs
- Pagination
- Error handling
- Authentication requirements (all endpoints require JWT tokens)
"""

import pytest

from gefapi.models.boundary import (
    AdminBoundary0Metadata,
    AdminBoundary1Metadata,
    AdminBoundary1Unit,
)


@pytest.fixture(autouse=True)
def clear_boundaries_before_each_test(db_session):
    """Clear boundary tables before each test to prevent duplicate key errors."""
    db_session.query(AdminBoundary1Unit).delete()
    db_session.query(AdminBoundary1Metadata).delete()
    db_session.query(AdminBoundary0Metadata).delete()
    db_session.commit()
    yield
    db_session.rollback()


@pytest.fixture
def sample_adm0_boundaries(db_session):
    """Create sample ADM0 boundary metadata for testing."""
    countries = [
        AdminBoundary0Metadata(
            boundaryISO="USA",
            releaseType="gbOpen",
            boundaryID="USA-ADM0-1_0",
            boundaryName="United States of America",
            boundaryType="ADM0",
            Continent="North America",
            buildDate="2021-01-01",
            gjDownloadURL="https://geoboundaries.org/data/USA/ADM0.geojson",
            tjDownloadURL="https://geoboundaries.org/data/USA/ADM0.topojson",
            staticDownloadLink="https://geoboundaries.org/data/USA/ADM0.zip",
            boundarySource="Natural Earth",
            boundaryLicense="CC BY 4.0",
        ),
        AdminBoundary0Metadata(
            boundaryISO="CAN",
            releaseType="gbOpen",
            boundaryID="CAN-ADM0-1_0",
            boundaryName="Canada",
            boundaryType="ADM0",
            Continent="North America",
            buildDate="2021-01-01",
            gjDownloadURL="https://geoboundaries.org/data/CAN/ADM0.geojson",
            tjDownloadURL="https://geoboundaries.org/data/CAN/ADM0.topojson",
            staticDownloadLink="https://geoboundaries.org/data/CAN/ADM0.zip",
            boundarySource="Natural Earth",
            boundaryLicense="CC BY 4.0",
        ),
        AdminBoundary0Metadata(
            boundaryISO="GBR",
            releaseType="gbOpen",
            boundaryID="GBR-ADM0-1_0",
            boundaryName="United Kingdom",
            boundaryType="ADM0",
            Continent="Europe",
            buildDate="2021-01-01",
            gjDownloadURL="https://geoboundaries.org/data/GBR/ADM0.geojson",
            tjDownloadURL="https://geoboundaries.org/data/GBR/ADM0.topojson",
            staticDownloadLink="https://geoboundaries.org/data/GBR/ADM0.zip",
            boundarySource="Natural Earth",
            boundaryLicense="CC BY 4.0",
        ),
    ]

    for country in countries:
        db_session.add(country)
    db_session.commit()

    return countries


@pytest.fixture
def sample_adm1_boundaries(db_session):
    """Create sample ADM1 boundary metadata and units for testing."""
    # Create ADM1 metadata for USA
    usa_adm1_metadata = AdminBoundary1Metadata(
        boundaryISO="USA",
        releaseType="gbOpen",
        boundaryID="USA-ADM1-1_0",
        boundaryName="United States of America",
        boundaryType="ADM1",
        Continent="North America",
        buildDate="2021-01-01",
        gjDownloadURL="https://geoboundaries.org/data/USA/ADM1.geojson",
        tjDownloadURL="https://geoboundaries.org/data/USA/ADM1.topojson",
        staticDownloadLink="https://geoboundaries.org/data/USA/ADM1.zip",
        boundarySource="US Census Bureau",
        boundaryLicense="CC BY 4.0",
        admUnitCount=56,
    )

    # Create individual ADM1 units
    adm1_units = [
        AdminBoundary1Unit(
            shapeID="USA-ADM1-3_0_0",
            releaseType="gbOpen",
            boundaryISO="USA",
            shapeName="California",
        ),
        AdminBoundary1Unit(
            shapeID="USA-ADM1-36_0_0",
            releaseType="gbOpen",
            boundaryISO="USA",
            shapeName="New York",
        ),
        AdminBoundary1Unit(
            shapeID="CAN-ADM1-ON_0_0",
            releaseType="gbOpen",
            boundaryISO="CAN",
            shapeName="Ontario",
        ),
    ]

    db_session.add(usa_adm1_metadata)
    for unit in adm1_units:
        db_session.add(unit)
    db_session.commit()

    return adm1_units


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
        assert data["boundaries"] == []
        assert data["release_type"] == "gbOpen"
        assert data["last_updated"] is None

    def test_get_adm0_boundaries(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test retrieving all ADM0 boundaries."""
        response = client.get(
            "/api/v1/data/boundaries?level=0", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["boundaries"]) == 3
        assert all(b["boundaryType"] == "ADM0" for b in data["boundaries"])
        assert data["release_type"] == "gbOpen"
        assert "last_updated" in data

    def test_get_adm1_boundaries(
        self, client, user_token, sample_adm1_boundaries, db_session
    ):
        """Test retrieving all ADM1 boundaries."""
        response = client.get(
            "/api/v1/data/boundaries?level=1", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["boundaries"]) == 3
        # ADM1 units don't have boundaryType field, check shapeID instead
        assert all("shapeID" in b for b in data["boundaries"])
        assert data["release_type"] == "gbOpen"

    def test_get_boundaries_mixed_levels(
        self,
        client,
        user_token,
        sample_adm0_boundaries,
        sample_adm1_boundaries,
        db_session,
    ):
        """Test retrieving boundaries from both levels."""
        response = client.get(
            "/api/v1/data/boundaries?level=0,1", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["boundaries"]) == 6  # 3 ADM0 + 3 ADM1
        assert data["release_type"] == "gbOpen"

    def test_filter_by_iso_code(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test filtering boundaries by ISO code."""
        response = client.get(
            "/api/v1/data/boundaries?iso=USA", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["boundaries"]) == 1
        assert data["boundaries"][0]["boundaryISO"] == "USA"

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
        assert len(data["boundaries"]) == 1
        assert data["boundaries"][0]["boundaryName"] == "Canada"

    def test_filter_by_name_adm1_not_supported(
        self, client, user_token, sample_adm1_boundaries, db_session
    ):
        """Test that name filtering is not supported for ADM1 level."""
        response = client.get(
            "/api/v1/data/boundaries?level=1&name=California",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "Name filtering is not supported for level=1" in data["detail"]

    def test_filter_by_release_type(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test filtering boundaries by release type."""
        response = client.get(
            "/api/v1/data/boundaries?release_type=gbOpen",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["release_type"] == "gbOpen"
        assert all(b["releaseType"] == "gbOpen" for b in data["boundaries"])

    def test_invalid_release_type(self, client, user_token):
        """Test handling of invalid release type."""
        response = client.get(
            "/api/v1/data/boundaries?release_type=invalid",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 400

    def test_download_urls_present(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test that download URLs are included in response."""
        response = client.get(
            "/api/v1/data/boundaries?iso=USA",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        boundary = data["boundaries"][0]
        assert "gjDownloadURL" in boundary
        assert "tjDownloadURL" in boundary
        assert boundary["gjDownloadURL"] is not None

    def test_pagination(self, client, user_token, sample_adm0_boundaries, db_session):
        """Test pagination of boundary results."""
        response = client.get(
            "/api/v1/data/boundaries?level=0&page=1&per_page=2",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["boundaries"]) == 2
        assert "meta" in data
        assert data["meta"]["total"] == 3
        assert data["meta"]["page"] == 1
        assert data["meta"]["per_page"] == 2
        assert data["meta"]["has_more"] is True

    def test_invalid_level_parameter(self, client, user_token):
        """Test handling of invalid administrative level."""
        response = client.get(
            "/api/v1/data/boundaries?level=5", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 400

    def test_invalid_level_format(self, client, user_token):
        """Test handling of invalid level format."""
        response = client.get(
            "/api/v1/data/boundaries?level=invalid",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 400

    def test_invalid_pagination_parameters(self, client, user_token):
        """Test handling of invalid pagination parameters."""
        response = client.get(
            "/api/v1/data/boundaries?page=invalid",
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
        data = response.get_json()
        assert "boundaries" in data

    def test_filter_by_updated_timestamp(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test filtering boundaries by update timestamp."""
        response = client.get(
            "/api/v1/data/boundaries?updated_at_since=2020-01-01T00:00:00Z",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert "boundaries" in data

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
            "/api/v1/data/boundaries?level=0&iso=USA",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["boundaries"]) == 1
        assert data["boundaries"][0]["boundaryISO"] == "USA"
        assert data["release_type"] == "gbOpen"


class TestBoundariesListEndpoint:
    """Tests for GET /api/v1/data/boundaries/list endpoint."""

    def _auth_headers(self, token):
        """Helper to create authorization headers."""
        return {"Authorization": f"Bearer {token}"}

    def test_requires_authentication(self, client):
        """Test that the list endpoint requires authentication."""
        response = client.get("/api/v1/data/boundaries/list")
        assert response.status_code == 401

    def test_get_boundaries_list(
        self,
        client,
        user_token,
        sample_adm0_boundaries,
        sample_adm1_boundaries,
        db_session,
    ):
        """Test retrieving hierarchical boundaries list."""
        response = client.get(
            "/api/v1/data/boundaries/list", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        data = response.get_json()
        assert "data" in data
        assert "release_type" in data
        assert "last_updated" in data
        assert data["release_type"] == "gbOpen"

    def test_list_with_release_type(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test list endpoint with specific release type."""
        response = client.get(
            "/api/v1/data/boundaries/list?release_type=gbOpen",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["release_type"] == "gbOpen"


class TestBoundariesLastUpdatedEndpoint:
    """Tests for GET /api/v1/data/boundaries/last-updated endpoint."""

    def _auth_headers(self, token):
        """Helper to create authorization headers."""
        return {"Authorization": f"Bearer {token}"}

    def test_requires_authentication(self, client):
        """Test that the last-updated endpoint requires authentication."""
        response = client.get("/api/v1/data/boundaries/last-updated")
        assert response.status_code == 401

    def test_get_last_updated_no_data(self, client, user_token):
        """Test last-updated endpoint with no data."""
        response = client.get(
            "/api/v1/data/boundaries/last-updated",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 404

    def test_get_last_updated_with_data(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test last-updated endpoint with data."""
        response = client.get(
            "/api/v1/data/boundaries/last-updated",
            headers=self._auth_headers(user_token),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert "data" in data
        assert "last_updated" in data["data"]
        assert "release_type" in data["data"]


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
        assert len(response.get_json()["boundaries"]) == 6

        # Verify boundary data is accessible
        data = response.get_json()["boundaries"]
        assert any(b["boundaryISO"] == "USA" for b in data)
        assert any(b["boundaryISO"] == "GBR" for b in data)

        # Query specific boundary
        response = client.get(
            "/api/v1/data/boundaries?iso=CAN", headers=self._auth_headers(user_token)
        )
        assert response.status_code == 200
        data = response.get_json()["boundaries"]
        assert len(data) == 1
        assert data[0]["boundaryName"] == "Canada"

    def test_response_structure_consistency(
        self, client, user_token, sample_adm0_boundaries, db_session
    ):
        """Test that all endpoints return consistent response structures."""
        # Main boundaries endpoint
        response1 = client.get(
            "/api/v1/data/boundaries", headers=self._auth_headers(user_token)
        )
        assert response1.status_code == 200
        data1 = response1.get_json()
        assert "boundaries" in data1
        assert "release_type" in data1
        assert "last_updated" in data1
        assert "meta" in data1

        # List endpoint
        response2 = client.get(
            "/api/v1/data/boundaries/list", headers=self._auth_headers(user_token)
        )
        assert response2.status_code == 200
        data2 = response2.get_json()
        assert "data" in data2
        assert "release_type" in data2
        assert "last_updated" in data2

        # Last updated endpoint
        response3 = client.get(
            "/api/v1/data/boundaries/last-updated",
            headers=self._auth_headers(user_token),
        )
        assert response3.status_code == 200
        data3 = response3.get_json()
        assert "data" in data3
        assert "last_updated" in data3["data"]
        assert "release_type" in data3["data"]
