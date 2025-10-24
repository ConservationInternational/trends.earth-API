"""
Tests for boundary models (AdminBoundary0 and AdminBoundary1).

These tests verify the boundary model functionality including:
- Model creation and validation with geoBoundaries API field names
- Storage and retrieval of download URLs
- Query operations
- Serialization (to_dict) with exact geoBoundaries field names

Note: Geometries are NOT stored - only metadata and download URLs.
"""

import pytest

from gefapi.models.boundary import AdminBoundary0, AdminBoundary1


@pytest.fixture(autouse=True)
def clear_boundaries(db_session):
    """Clear boundary tables before each test to prevent duplicate key errors."""
    # Delete all boundaries before each test
    db_session.query(AdminBoundary1).delete()
    db_session.query(AdminBoundary0).delete()
    db_session.commit()
    yield
    # Cleanup after test (transaction will rollback anyway)
    db_session.rollback()


class TestAdminBoundary0Model:
    """Tests for AdminBoundary0 (country-level) model."""

    def test_create_adm0_minimal(self, db_session):
        """Test creating an ADM0 boundary with minimal required fields."""
        boundary = AdminBoundary0(boundaryISO="USA")
        db_session.add(boundary)
        db_session.commit()

        # Verify it was created
        assert boundary.boundaryISO == "USA"
        assert AdminBoundary0.query.filter_by(boundaryISO="USA").first() is not None

    def test_create_adm0_with_metadata(self, db_session):
        """Test creating an ADM0 boundary with geoBoundaries API metadata."""
        boundary = AdminBoundary0(
            boundaryISO="FRA",
            boundaryID="FRA-ADM0-12345",
            boundaryName="France",
            boundaryType="ADM0",
            boundaryCanonical="France",
            boundarySource="Natural Earth",
            boundaryLicense="CC BY 4.0",
            licenseDetail="https://creativecommons.org/licenses/by/4.0/",
            licenseSource="Natural Earth",
            sourceDataUpdateDate="2024-01-15",
            buildDate="2024-01-20",
            Continent="Europe",
            UNSDG_region="Europe",
            UNSDG_subregion="Western Europe",
            worldBankIncomeGroup="High income",
            admUnitCount=1,
            meanVertices=1500.5,
            minVertices=1500,
            maxVertices=1501,
            meanPerimeterLengthKM=3500.5,
            minPerimeterLengthKM=3500.0,
            maxPerimeterLengthKM=3501.0,
            meanAreaSqKM=551695.0,
            minAreaSqKM=551695.0,
            maxAreaSqKM=551695.0,
            staticDownloadLink="https://example.com/fra.zip",
            gjDownloadURL="https://example.com/fra.geojson",
            tjDownloadURL="https://example.com/fra.topojson",
            simplifiedGeometryGeoJSON="https://example.com/fra_simple.geojson",
            imagePreview="https://example.com/fra.png",
        )
        db_session.add(boundary)
        db_session.commit()

        # Verify metadata was stored correctly
        saved = AdminBoundary0.query.filter_by(boundaryISO="FRA").first()
        assert saved.boundaryName == "France"
        assert saved.Continent == "Europe"
        assert saved.worldBankIncomeGroup == "High income"
        assert saved.meanAreaSqKM == 551695.0
        assert saved.tjDownloadURL == "https://example.com/fra.topojson"

    def test_create_adm0_with_download_urls(self, db_session):
        """Test creating an ADM0 boundary with download URLs."""
        boundary = AdminBoundary0(
            boundaryISO="TST",
            gjDownloadURL="https://example.com/test.geojson",
            tjDownloadURL="https://example.com/test.topojson",
        )
        db_session.add(boundary)
        db_session.commit()

        # Verify download URLs were stored
        saved = AdminBoundary0.query.filter_by(boundaryISO="TST").first()
        assert saved.gjDownloadURL == "https://example.com/test.geojson"
        assert saved.tjDownloadURL == "https://example.com/test.topojson"

    def test_adm0_to_dict(self, db_session):
        """Test ADM0 model to_dict serialization with geoBoundaries field names."""
        boundary = AdminBoundary0(
            boundaryISO="DEU",
            boundaryName="Germany",
            Continent="Europe",
            admUnitCount=16,
            gjDownloadURL="https://example.com/deu.geojson",
        )
        db_session.add(boundary)
        db_session.commit()

        # Serialize to dictionary
        data = boundary.to_dict()

        # Verify exact geoBoundaries field names are used
        assert data["boundaryISO"] == "DEU"
        assert data["boundaryName"] == "Germany"
        assert data["Continent"] == "Europe"
        assert data["admUnitCount"] == 16
        assert data["gjDownloadURL"] == "https://example.com/deu.geojson"

    def test_adm0_query_by_iso(self, db_session):
        """Test querying ADM0 boundaries by ISO code."""
        # Create multiple boundaries
        db_session.add(AdminBoundary0(boundaryISO="CAN", boundaryName="Canada"))
        db_session.add(AdminBoundary0(boundaryISO="MEX", boundaryName="Mexico"))
        db_session.add(AdminBoundary0(boundaryISO="USA", boundaryName="United States"))
        db_session.commit()

        # Query by ISO code
        result = AdminBoundary0.query.filter_by(boundaryISO="CAN").first()
        assert result is not None
        assert result.boundaryName == "Canada"

    def test_adm0_update_fields(self, db_session):
        """Test updating ADM0 boundary fields."""
        # Create boundary
        boundary = AdminBoundary0(boundaryISO="ITA", boundaryName="Italy Old")
        db_session.add(boundary)
        db_session.commit()

        # Update fields
        boundary.boundaryName = "Italy Updated"
        boundary.Continent = "Europe"
        db_session.commit()

        # Verify updates
        updated = AdminBoundary0.query.filter_by(boundaryISO="ITA").first()
        assert updated.boundaryName == "Italy Updated"
        assert updated.Continent == "Europe"

    def test_adm0_nullable_fields(self, db_session):
        """Test that metadata fields are nullable."""
        # Create boundary with only required field
        boundary = AdminBoundary0(boundaryISO="TST")
        db_session.add(boundary)
        db_session.commit()

        # Verify it was created without errors
        saved = AdminBoundary0.query.filter_by(boundaryISO="TST").first()
        assert saved.boundaryName is None
        assert saved.Continent is None
        assert saved.admUnitCount is None


class TestAdminBoundary1Model:
    """Tests for AdminBoundary1 (state/province-level) model."""

    def test_create_adm1_minimal(self, db_session):
        """Test creating an ADM1 boundary with minimal required fields."""
        boundary = AdminBoundary1(
            shapeID="USA-ADM1-CA",
            boundaryISO="USA",  # Country code
        )
        db_session.add(boundary)
        db_session.commit()

        # Verify it was created
        assert boundary.shapeID == "USA-ADM1-CA"
        assert boundary.boundaryISO == "USA"
        assert AdminBoundary1.query.filter_by(shapeID="USA-ADM1-CA").first() is not None

    def test_create_adm1_with_metadata(self, db_session):
        """Test creating an ADM1 boundary with geoBoundaries API metadata."""
        boundary = AdminBoundary1(
            shapeID="USA-ADM1-NY",
            boundaryISO="USA",
            boundaryID="USA-ADM1-12345",
            boundaryName="New York",
            boundaryType="ADM1",
            boundaryCanonical="United States of America",
            boundarySource="TIGER/Line",
            boundaryLicense="Public Domain",
            licenseDetail="https://www.census.gov/",
            licenseSource="US Census Bureau",
            sourceDataUpdateDate="2024-01-10",
            buildDate="2024-01-15",
            Continent="North America",
            UNSDG_region="Americas",
            UNSDG_subregion="Northern America",
            worldBankIncomeGroup="High income",
            admUnitCount=50,
            meanVertices=500.5,
            minVertices=100,
            maxVertices=1000,
            meanPerimeterLengthKM=800.0,
            minPerimeterLengthKM=200.0,
            maxPerimeterLengthKM=1500.0,
            meanAreaSqKM=200000.0,
            minAreaSqKM=1000.0,
            maxAreaSqKM=800000.0,
            staticDownloadLink="https://example.com/usa_adm1.zip",
            gjDownloadURL="https://example.com/usa_adm1.geojson",
            tjDownloadURL="https://example.com/usa_adm1.topojson",
            simplifiedGeometryGeoJSON="https://example.com/usa_adm1_simple.geojson",
            imagePreview="https://example.com/usa_adm1.png",
        )
        db_session.add(boundary)
        db_session.commit()

        # Verify metadata was stored correctly
        saved = AdminBoundary1.query.filter_by(shapeID="USA-ADM1-NY").first()
        assert saved.boundaryISO == "USA"
        assert saved.boundaryName == "New York"
        assert saved.Continent == "North America"
        assert saved.admUnitCount == 50
        assert saved.tjDownloadURL == "https://example.com/usa_adm1.topojson"

    def test_create_adm1_with_download_urls(self, db_session):
        """Test creating an ADM1 boundary with download URLs."""
        boundary = AdminBoundary1(
            shapeID="USA-ADM1-CA",
            boundaryISO="USA",
            gjDownloadURL="https://example.com/usa_adm1.geojson",
        )
        db_session.add(boundary)
        db_session.commit()

        # Verify download URL was stored
        saved = AdminBoundary1.query.filter_by(shapeID="USA-ADM1-CA").first()
        assert saved.gjDownloadURL == "https://example.com/usa_adm1.geojson"

    def test_adm1_to_dict(self, db_session):
        """Test ADM1 model to_dict serialization with geoBoundaries field names."""
        boundary = AdminBoundary1(
            shapeID="CAN-ADM1-ON",
            boundaryISO="CAN",
            boundaryName="Ontario",
            Continent="North America",
            admUnitCount=13,
            gjDownloadURL="https://example.com/can_adm1.geojson",
        )
        db_session.add(boundary)
        db_session.commit()

        # Serialize to dictionary
        data = boundary.to_dict()

        # Verify exact geoBoundaries field names are used
        assert data["shapeID"] == "CAN-ADM1-ON"
        assert data["boundaryISO"] == "CAN"
        assert data["boundaryName"] == "Ontario"
        assert data["Continent"] == "North America"
        assert data["admUnitCount"] == 13
        assert data["gjDownloadURL"] == "https://example.com/can_adm1.geojson"

    def test_adm1_query_by_country(self, db_session):
        """Test querying ADM1 boundaries by country code."""
        # Create multiple ADM1 boundaries for different countries
        db_session.add(AdminBoundary1(shapeID="USA-ADM1-CA", boundaryISO="USA"))
        db_session.add(AdminBoundary1(shapeID="USA-ADM1-NY", boundaryISO="USA"))
        db_session.add(AdminBoundary1(shapeID="CAN-ADM1-ON", boundaryISO="CAN"))
        db_session.commit()

        # Query all ADM1 boundaries for USA
        results = AdminBoundary1.query.filter_by(boundaryISO="USA").all()
        assert len(results) == 2
        assert all(r.boundaryISO == "USA" for r in results)

    def test_adm1_update_fields(self, db_session):
        """Test updating ADM1 boundary fields."""
        # Create boundary
        boundary = AdminBoundary1(
            shapeID="MEX-ADM1-01", boundaryISO="MEX", boundaryName="State Old"
        )
        db_session.add(boundary)
        db_session.commit()

        # Update fields
        boundary.boundaryName = "State Updated"
        boundary.Continent = "North America"
        db_session.commit()

        # Verify updates
        updated = AdminBoundary1.query.filter_by(shapeID="MEX-ADM1-01").first()
        assert updated.boundaryName == "State Updated"
        assert updated.Continent == "North America"

    def test_adm1_nullable_fields(self, db_session):
        """Test that metadata fields are nullable."""
        # Create boundary with only required fields
        boundary = AdminBoundary1(shapeID="TST-ADM1-01", boundaryISO="TST")
        db_session.add(boundary)
        db_session.commit()

        # Verify it was created without errors
        saved = AdminBoundary1.query.filter_by(shapeID="TST-ADM1-01").first()
        assert saved.boundaryName is None
        assert saved.Continent is None
        assert saved.admUnitCount is None

    def test_adm1_multiple_per_country(self, db_session):
        """Test storing multiple ADM1 boundaries for the same country."""
        # Create multiple states for USA
        states = ["CA", "NY", "TX", "FL", "IL"]
        for state_code in states:
            boundary = AdminBoundary1(
                shapeID=f"USA-ADM1-{state_code}",
                boundaryISO="USA",
            )
            db_session.add(boundary)
        db_session.commit()

        # Verify all were created
        results = AdminBoundary1.query.filter_by(boundaryISO="USA").all()
        assert len(results) == 5
        shape_ids = {r.shapeID for r in results}
        assert "USA-ADM1-CA" in shape_ids
        assert "USA-ADM1-NY" in shape_ids
        assert "USA-ADM1-TX" in shape_ids


class TestBoundaryModelsRelationship:
    """Tests for relationships between ADM0 and ADM1 boundaries."""

    def test_adm0_and_adm1_relationship(self, db_session):
        """Test querying ADM1 boundaries for a specific ADM0 country."""
        # Create ADM0 country
        country = AdminBoundary0(boundaryISO="BRA", boundaryName="Brazil")
        db_session.add(country)

        # Create ADM1 states
        states = [
            AdminBoundary1(shapeID="BRA-ADM1-SP", boundaryISO="BRA"),
            AdminBoundary1(shapeID="BRA-ADM1-RJ", boundaryISO="BRA"),
            AdminBoundary1(shapeID="BRA-ADM1-MG", boundaryISO="BRA"),
        ]
        for state in states:
            db_session.add(state)
        db_session.commit()

        # Query ADM1 boundaries for Brazil
        adm1_boundaries = AdminBoundary1.query.filter_by(boundaryISO="BRA").all()
        assert len(adm1_boundaries) == 3

        # Verify country exists
        adm0_boundary = AdminBoundary0.query.filter_by(boundaryISO="BRA").first()
        assert adm0_boundary is not None
        assert adm0_boundary.boundaryName == "Brazil"

    def test_download_urls_stored_correctly(self, db_session):
        """Test that download URLs are stored and retrievable."""
        # Create ADM0 with download URLs
        country = AdminBoundary0(
            boundaryISO="USA",
            boundaryName="United States",
            gjDownloadURL="https://geoboundaries.org/data/USA-ADM0.geojson",
            tjDownloadURL="https://geoboundaries.org/data/USA-ADM0.topojson",
        )
        db_session.add(country)

        # Create ADM1 with download URLs
        state = AdminBoundary1(
            shapeID="USA-ADM1-CA",
            boundaryISO="USA",
            boundaryName="California",
            gjDownloadURL="https://geoboundaries.org/data/USA-ADM1.geojson",
        )
        db_session.add(state)
        db_session.commit()

        # Verify URLs are retrievable
        saved_country = AdminBoundary0.query.filter_by(boundaryISO="USA").first()
        assert (
            saved_country.gjDownloadURL
            == "https://geoboundaries.org/data/USA-ADM0.geojson"
        )

        saved_state = AdminBoundary1.query.filter_by(shapeID="USA-ADM1-CA").first()
        assert (
            saved_state.gjDownloadURL
            == "https://geoboundaries.org/data/USA-ADM1.geojson"
        )
