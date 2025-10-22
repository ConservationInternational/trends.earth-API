"""
Tests for boundary models (AdminBoundary0 and AdminBoundary1).

These tests verify the boundary model functionality including:
- Model creation and validation
- GeoBoundaries API metadata fields
- Geometry handling (PostGIS MULTIPOLYGON)
- Serialization (to_dict)
- Query operations
"""

from geoalchemy2 import WKTElement
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
        boundary = AdminBoundary0(id="USA")
        db_session.add(boundary)
        db_session.commit()

        # Verify it was created
        assert boundary.id == "USA"
        assert AdminBoundary0.query.filter_by(id="USA").first() is not None

    def test_create_adm0_with_metadata(self, db_session):
        """Test creating an ADM0 boundary with GeoBoundaries API metadata."""
        boundary = AdminBoundary0(
            id="FRA",
            boundary_id="FRA-ADM0-12345",
            boundary_name="France",
            boundary_iso="FRA",
            boundary_type="ADM0",
            boundary_canonical="France",
            boundary_source="Natural Earth",
            boundary_license="CC BY 4.0",
            license_detail="https://creativecommons.org/licenses/by/4.0/",
            license_source="Natural Earth",
            source_data_update_date="2024-01-15",
            build_date="2024-01-20",
            continent="Europe",
            unsdg_region="Europe",
            unsdg_subregion="Western Europe",
            world_bank_income_group="High income",
            adm_unit_count=1,
            mean_vertices=1500.5,
            min_vertices=1500,
            max_vertices=1501,
            mean_perimeter_km=3500.5,
            min_perimeter_km=3500.0,
            max_perimeter_km=3501.0,
            mean_area_sqkm=551695.0,
            min_area_sqkm=551695.0,
            max_area_sqkm=551695.0,
            static_download_link="https://example.com/fra.zip",
            geojson_download_url="https://example.com/fra.geojson",
            topojson_download_url="https://example.com/fra.topojson",
            simplified_geojson_url="https://example.com/fra_simple.geojson",
            image_preview_url="https://example.com/fra.png",
        )
        db_session.add(boundary)
        db_session.commit()

        # Verify metadata was stored correctly
        saved = AdminBoundary0.query.filter_by(id="FRA").first()
        assert saved.boundary_name == "France"
        assert saved.boundary_iso == "FRA"
        assert saved.continent == "Europe"
        assert saved.world_bank_income_group == "High income"
        assert saved.mean_area_sqkm == 551695.0
        assert saved.topojson_download_url == "https://example.com/fra.topojson"

    def test_create_adm0_with_geometry(self, db_session):
        """Test creating an ADM0 boundary with PostGIS geometry."""
        # Simple polygon WKT for testing (approximation of a small country)
        wkt = "MULTIPOLYGON(((-10 40, -10 50, 0 50, 0 40, -10 40)))"
        geometry = WKTElement(wkt, srid=4326)

        boundary = AdminBoundary0(id="TST", geometry=geometry)
        db_session.add(boundary)
        db_session.commit()

        # Verify geometry was stored
        saved = AdminBoundary0.query.filter_by(id="TST").first()
        assert saved.geometry is not None

    def test_adm0_to_dict(self, db_session):
        """Test ADM0 model to_dict serialization."""
        boundary = AdminBoundary0(
            id="DEU",
            boundary_name="Germany",
            boundary_iso="DEU",
            continent="Europe",
            adm_unit_count=16,
        )
        db_session.add(boundary)
        db_session.commit()

        # Serialize to dictionary
        data = boundary.to_dict()

        # Verify key fields are present (camelCase keys from to_dict)
        assert data["id"] == "DEU"
        assert data["boundaryName"] == "Germany"
        assert data["boundaryISO"] == "DEU"
        # Note: continent not included in basic to_dict, need include_metadata=True

    def test_adm0_query_by_iso(self, db_session):
        """Test querying ADM0 boundaries by ISO code."""
        # Create multiple boundaries
        db_session.add(AdminBoundary0(id="CAN", boundary_iso="CAN"))
        db_session.add(AdminBoundary0(id="MEX", boundary_iso="MEX"))
        db_session.add(AdminBoundary0(id="USA", boundary_iso="USA"))
        db_session.commit()

        # Query by ISO code
        result = AdminBoundary0.query.filter_by(boundary_iso="CAN").first()
        assert result is not None
        assert result.id == "CAN"

    def test_adm0_update_fields(self, db_session):
        """Test updating ADM0 boundary fields."""
        # Create boundary
        boundary = AdminBoundary0(id="ITA", boundary_name="Italy Old")
        db_session.add(boundary)
        db_session.commit()

        # Update fields
        boundary.boundary_name = "Italy Updated"
        boundary.continent = "Europe"
        db_session.commit()

        # Verify updates
        updated = AdminBoundary0.query.filter_by(id="ITA").first()
        assert updated.boundary_name == "Italy Updated"
        assert updated.continent == "Europe"

    def test_adm0_nullable_fields(self, db_session):
        """Test that metadata fields are nullable."""
        # Create boundary with only required field
        boundary = AdminBoundary0(id="TST")
        db_session.add(boundary)
        db_session.commit()

        # Verify it was created without errors
        saved = AdminBoundary0.query.filter_by(id="TST").first()
        assert saved.boundary_name is None
        assert saved.continent is None
        assert saved.adm_unit_count is None

    def test_adm0_no_legacy_fields_in_columns(self, db_session):
        """Test that legacy fields (shape_group, shape_type, shape_name) are not database columns."""
        boundary = AdminBoundary0(id="TST")
        db_session.add(boundary)
        db_session.commit()

        # Verify the model doesn't have these attributes as columns
        # (they may exist in to_dict() for backward compatibility)
        columns = [col.name for col in AdminBoundary0.__table__.columns]
        assert "shape_group" not in columns
        assert "shape_type" not in columns
        assert "shape_name" not in columns


class TestAdminBoundary1Model:
    """Tests for AdminBoundary1 (state/province-level) model."""

    def test_create_adm1_minimal(self, db_session):
        """Test creating an ADM1 boundary with minimal required fields."""
        boundary = AdminBoundary1(
            shape_id="USA-ADM1-CA",
            id="USA",  # Country code
        )
        db_session.add(boundary)
        db_session.commit()

        # Verify it was created
        assert boundary.shape_id == "USA-ADM1-CA"
        assert boundary.id == "USA"
        assert (
            AdminBoundary1.query.filter_by(shape_id="USA-ADM1-CA").first() is not None
        )

    def test_create_adm1_with_metadata(self, db_session):
        """Test creating an ADM1 boundary with GeoBoundaries API metadata."""
        boundary = AdminBoundary1(
            shape_id="USA-ADM1-NY",
            id="USA",
            boundary_id="USA-ADM1-12345",
            boundary_name="United States",
            boundary_iso="USA",
            boundary_type="ADM1",
            boundary_canonical="United States of America",
            boundary_source="TIGER/Line",
            boundary_license="Public Domain",
            license_detail="https://www.census.gov/",
            license_source="US Census Bureau",
            source_data_update_date="2024-01-10",
            build_date="2024-01-15",
            continent="North America",
            unsdg_region="Americas",
            unsdg_subregion="Northern America",
            world_bank_income_group="High income",
            adm_unit_count=50,
            mean_vertices=500.5,
            min_vertices=100,
            max_vertices=1000,
            mean_perimeter_km=800.0,
            min_perimeter_km=200.0,
            max_perimeter_km=1500.0,
            mean_area_sqkm=200000.0,
            min_area_sqkm=1000.0,
            max_area_sqkm=800000.0,
            static_download_link="https://example.com/usa_adm1.zip",
            geojson_download_url="https://example.com/usa_adm1.geojson",
            topojson_download_url="https://example.com/usa_adm1.topojson",
            simplified_geojson_url="https://example.com/usa_adm1_simple.geojson",
            image_preview_url="https://example.com/usa_adm1.png",
        )
        db_session.add(boundary)
        db_session.commit()

        # Verify metadata was stored correctly
        saved = AdminBoundary1.query.filter_by(shape_id="USA-ADM1-NY").first()
        assert saved.id == "USA"
        assert saved.boundary_name == "United States"
        assert saved.continent == "North America"
        assert saved.adm_unit_count == 50
        assert saved.topojson_download_url == "https://example.com/usa_adm1.topojson"

    def test_create_adm1_with_geometry(self, db_session):
        """Test creating an ADM1 boundary with PostGIS geometry."""
        # Simple polygon WKT for testing (approximation of a state)
        wkt = "MULTIPOLYGON(((-120 35, -120 42, -114 42, -114 35, -120 35)))"
        geometry = WKTElement(wkt, srid=4326)

        boundary = AdminBoundary1(shape_id="USA-ADM1-CA", id="USA", geometry=geometry)
        db_session.add(boundary)
        db_session.commit()

        # Verify geometry was stored
        saved = AdminBoundary1.query.filter_by(shape_id="USA-ADM1-CA").first()
        assert saved.geometry is not None

    def test_adm1_to_dict(self, db_session):
        """Test ADM1 model to_dict serialization."""
        boundary = AdminBoundary1(
            shape_id="CAN-ADM1-ON",
            id="CAN",
            boundary_name="Canada",
            continent="North America",
            adm_unit_count=13,
        )
        db_session.add(boundary)
        db_session.commit()

        # Serialize to dictionary
        data = boundary.to_dict()

        # Verify key fields are present (camelCase keys from to_dict)
        assert data["shapeId"] == "CAN-ADM1-ON"
        assert data["id"] == "CAN"
        assert data["boundaryName"] == "Canada"
        # Note: continent not included in basic to_dict, need include_metadata=True

    def test_adm1_query_by_country(self, db_session):
        """Test querying ADM1 boundaries by country code."""
        # Create multiple ADM1 boundaries for different countries
        db_session.add(AdminBoundary1(shape_id="USA-ADM1-CA", id="USA"))
        db_session.add(AdminBoundary1(shape_id="USA-ADM1-NY", id="USA"))
        db_session.add(AdminBoundary1(shape_id="CAN-ADM1-ON", id="CAN"))
        db_session.commit()

        # Query all ADM1 boundaries for USA
        results = AdminBoundary1.query.filter_by(id="USA").all()
        assert len(results) == 2
        assert all(r.id == "USA" for r in results)

    def test_adm1_update_fields(self, db_session):
        """Test updating ADM1 boundary fields."""
        # Create boundary
        boundary = AdminBoundary1(
            shape_id="MEX-ADM1-01", id="MEX", boundary_name="Mexico Old"
        )
        db_session.add(boundary)
        db_session.commit()

        # Update fields
        boundary.boundary_name = "Mexico Updated"
        boundary.continent = "North America"
        db_session.commit()

        # Verify updates
        updated = AdminBoundary1.query.filter_by(shape_id="MEX-ADM1-01").first()
        assert updated.boundary_name == "Mexico Updated"
        assert updated.continent == "North America"

    def test_adm1_nullable_fields(self, db_session):
        """Test that metadata fields are nullable."""
        # Create boundary with only required fields
        boundary = AdminBoundary1(shape_id="TST-ADM1-01", id="TST")
        db_session.add(boundary)
        db_session.commit()

        # Verify it was created without errors
        saved = AdminBoundary1.query.filter_by(shape_id="TST-ADM1-01").first()
        assert saved.boundary_name is None
        assert saved.continent is None
        assert saved.adm_unit_count is None

    def test_adm1_no_legacy_fields_in_columns(self, db_session):
        """Test that legacy fields (shape_group, shape_type, shape_name) are not database columns."""
        boundary = AdminBoundary1(shape_id="TST-ADM1-01", id="TST")
        db_session.add(boundary)
        db_session.commit()

        # Verify the model doesn't have these attributes as columns
        # (they may exist in to_dict() for backward compatibility)
        columns = [col.name for col in AdminBoundary1.__table__.columns]
        assert "shape_group" not in columns
        assert "shape_type" not in columns
        assert "shape_name" not in columns

    def test_adm1_multiple_per_country(self, db_session):
        """Test storing multiple ADM1 boundaries for the same country."""
        # Create multiple states for USA
        states = ["CA", "NY", "TX", "FL", "IL"]
        for state_code in states:
            boundary = AdminBoundary1(
                shape_id=f"USA-ADM1-{state_code}",
                id="USA",
                boundary_iso="USA",
            )
            db_session.add(boundary)
        db_session.commit()

        # Verify all were created
        results = AdminBoundary1.query.filter_by(id="USA").all()
        assert len(results) == 5
        shape_ids = {r.shape_id for r in results}
        assert "USA-ADM1-CA" in shape_ids
        assert "USA-ADM1-NY" in shape_ids
        assert "USA-ADM1-TX" in shape_ids


class TestBoundaryModelsRelationship:
    """Tests for relationships between ADM0 and ADM1 boundaries."""

    def test_adm0_and_adm1_relationship(self, db_session):
        """Test querying ADM1 boundaries for a specific ADM0 country."""
        # Create ADM0 country
        country = AdminBoundary0(id="BRA", boundary_name="Brazil")
        db_session.add(country)

        # Create ADM1 states
        states = [
            AdminBoundary1(shape_id="BRA-ADM1-SP", id="BRA"),
            AdminBoundary1(shape_id="BRA-ADM1-RJ", id="BRA"),
            AdminBoundary1(shape_id="BRA-ADM1-MG", id="BRA"),
        ]
        for state in states:
            db_session.add(state)
        db_session.commit()

        # Query ADM1 boundaries for Brazil
        adm1_boundaries = AdminBoundary1.query.filter_by(id="BRA").all()
        assert len(adm1_boundaries) == 3

        # Verify country exists
        adm0_boundary = AdminBoundary0.query.filter_by(id="BRA").first()
        assert adm0_boundary is not None
        assert adm0_boundary.boundary_name == "Brazil"
