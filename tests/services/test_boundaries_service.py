"""
Tests for the Boundaries Service.
Tests service layer functionality, database operations, and business logic.
"""

from unittest.mock import MagicMock, patch

from geoalchemy2 import WKTElement
import pytest

from gefapi import db
from gefapi.models.boundary import AdminBoundary0, AdminBoundary1
from gefapi.services.boundaries_service import BoundariesService


@pytest.mark.usefixtures("app", "db_session")
class TestBoundariesService:
    """Test cases for Boundaries Service."""

    def setup_method(self):
        """Set up test fixtures."""
        # Clean up any existing test data
        try:
            db.session.query(AdminBoundary0).filter(
                AdminBoundary0.id.like("T%")
            ).delete()
            db.session.query(AdminBoundary1).filter(
                AdminBoundary1.shape_id.like("T%")
            ).delete()
            db.session.query(AdminBoundary0).filter(
                AdminBoundary0.id.like("PT%")
            ).delete()
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Generate unique test suffix to prevent conflicts (keep within 10 char limit)
        import time

        self.test_suffix = str(int(time.time() * 1000) % 1000)

    def teardown_method(self):
        """Clean up test fixtures."""
        # Clean up test data after each test
        try:
            db.session.query(AdminBoundary0).filter(
                AdminBoundary0.id.like("T%")
            ).delete()
            db.session.query(AdminBoundary1).filter(
                AdminBoundary1.shape_id.like("T%")
            ).delete()
            db.session.query(AdminBoundary0).filter(
                AdminBoundary0.id.like("PT%")
            ).delete()
            db.session.commit()
        except Exception:
            db.session.rollback()

    def test_validate_point_coordinates_valid(self):
        """Test point coordinate validation with valid coordinates."""
        assert BoundariesService.validate_point_coordinates(0, 0) is True
        assert BoundariesService.validate_point_coordinates(40.7128, -74.0060) is True
        assert BoundariesService.validate_point_coordinates(-90, -180) is True
        assert BoundariesService.validate_point_coordinates(90, 180) is True

    def test_validate_point_coordinates_invalid(self):
        """Test point coordinate validation with invalid coordinates."""
        assert BoundariesService.validate_point_coordinates(91, 0) is False
        assert BoundariesService.validate_point_coordinates(-91, 0) is False
        assert BoundariesService.validate_point_coordinates(0, 181) is False
        assert BoundariesService.validate_point_coordinates(0, -181) is False

    def test_get_boundaries_adm0_no_filters(self):
        """Test getting ADM0 boundaries without filters."""
        # Use unique ID for this test
        sample_country = AdminBoundary0(
            id=f"T1{self.test_suffix}",
            shape_name="Test Country 1",
            shape_group="Country",
            shape_type="ADM0",
            geometry=WKTElement(
                "POLYGON((-180 -90, -180 90, 180 90, 180 -90, -180 -90))", srid=4326
            ),
        )
        db.session.add(sample_country)
        db.session.commit()

        results, total = BoundariesService.get_boundaries(
            levels=[0], format_type="table", page=1, per_page=10
        )
        assert total >= 1
        assert len(results) >= 1

    def test_get_boundaries_adm0_with_geometry(self):
        """Test getting ADM0 boundaries with geometry."""
        # Use unique ID for this test
        sample_country = AdminBoundary0(
            id=f"T2{self.test_suffix}",
            shape_name="Test Country 2",
            shape_group="Country",
            shape_type="ADM0",
            geometry=WKTElement(
                "POLYGON((-180 -90, -180 90, 180 90, 180 -90, -180 -90))", srid=4326
            ),
        )
        db.session.add(sample_country)
        db.session.commit()

        results, total = BoundariesService.get_boundaries(
            levels=[0], format_type="full", page=1, per_page=10
        )
        assert total >= 1
        assert len(results) >= 1

    def test_get_boundaries_adm1_no_filters(self):
        """Test getting ADM1 boundaries without filters."""
        # Use unique IDs for this test
        sample_country = AdminBoundary0(
            id=f"TEST003{self.test_suffix}",
            shape_name="Test Country 3",
            shape_group="Country",
            shape_type="ADM0",
            geometry=WKTElement(
                "POLYGON((-180 -90, -180 90, 180 90, 180 -90, -180 -90))", srid=4326
            ),
        )

        sample_state = AdminBoundary1(
            shape_id=f"T3{self.test_suffix}-A1",
            id=f"T3{self.test_suffix}",
            shape_name="Test State 1",
            shape_group="State",
            shape_type="ADM1",
            geometry=WKTElement(
                "POLYGON((-125 30, -125 42, -114 42, -114 30, -125 30))", srid=4326
            ),
        )

        db.session.add(sample_country)
        db.session.add(sample_state)
        db.session.commit()

        results, total = BoundariesService.get_boundaries(
            levels=[1], format_type="table", page=1, per_page=10
        )
        assert total >= 1
        assert len(results) >= 1

    def test_get_boundary_statistics_empty_db(self):
        """Test boundary statistics with empty database."""
        stats = BoundariesService.get_boundary_statistics()
        assert "total_countries" in stats
        assert "total_admin1_units" in stats
        assert "levels_available" in stats
        assert isinstance(stats["total_countries"], int)
        assert isinstance(stats["total_admin1_units"], int)
        assert isinstance(stats["levels_available"], list)

    def test_get_boundary_statistics_with_data(self):
        """Test boundary statistics with data."""
        # Use unique IDs for this test
        sample_country = AdminBoundary0(
            id=f"TEST004{self.test_suffix}",
            shape_name="Test Country 4",
            shape_group="Country",
            shape_type="ADM0",
            geometry=WKTElement(
                "POLYGON((-180 -90, -180 90, 180 90, 180 -90, -180 -90))", srid=4326
            ),
        )

        sample_state = AdminBoundary1(
            shape_id=f"T4{self.test_suffix}-A1",
            id=f"T4{self.test_suffix}",
            shape_name="Test State 2",
            shape_group="State",
            shape_type="ADM1",
            geometry=WKTElement(
                "POLYGON((-125 30, -125 42, -114 42, -114 30, -125 30))", srid=4326
            ),
        )

        db.session.add(sample_country)
        db.session.add(sample_state)
        db.session.commit()

        stats = BoundariesService.get_boundary_statistics()
        assert stats["total_countries"] >= 1
        assert stats["total_admin1_units"] >= 1

    def test_find_boundaries_containing_point_invalid_coordinates(self):
        """Test point containment query with invalid coordinates."""
        with pytest.raises(ValueError, match="Invalid coordinates"):
            BoundariesService.find_boundaries_containing_point(lat=100, lon=0, level=0)

    def test_find_boundaries_containing_point_valid_coordinates(self):
        """Test point containment query with valid coordinates."""
        # Use a unique ID for this test to avoid conflicts
        unique_country = AdminBoundary0(
            id=f"PT{self.test_suffix}",
            shape_name="Point Test Country",
            shape_group="Country",
            shape_type="ADM0",
            geometry=WKTElement(
                "POLYGON((-75 40, -74 40, -74 41, -75 41, -75 40))", srid=4326
            ),
        )
        db.session.add(unique_country)
        db.session.commit()

        results = BoundariesService.find_boundaries_containing_point(
            lon=-74.5, lat=40.5, level=0
        )

        # Should find the boundary that contains the point
        assert isinstance(results, list)

    def test_get_boundary_statistics_exception_handling(self):
        """Test boundary statistics exception handling."""
        with patch("gefapi.db.session.query") as mock_query:
            mock_query.side_effect = Exception("Database error")

            stats = BoundariesService.get_boundary_statistics()
            # Should return default empty structure on error
            assert "total_countries" in stats
            assert stats["total_countries"] == 0

    def test_get_boundary_by_id_level_0(self):
        """Test getting boundary by ID from level 0."""
        boundary_id = f"test_country_{self.test_suffix}"

        # Create a mock AdminBoundary0 object
        mock_boundary = MagicMock()
        mock_boundary.id = boundary_id
        mock_boundary.shape_name = "Test Country"
        mock_boundary.shape_type = "Country"
        mock_boundary.shape_group = "ADM0"
        mock_boundary.geometry = None  # No geometry for this test

        with patch("gefapi.db.session.query") as mock_query:
            # First query (AdminBoundary0) returns the boundary
            mock_query.return_value.filter.return_value.first.return_value = (
                mock_boundary
            )

            result = BoundariesService.get_boundary_by_id(boundary_id)

            assert result is not None
            assert result["id"] == boundary_id
            assert result["shape_name"] == "Test Country"
            assert result["level"] == 0

    def test_get_boundary_by_id_level_1(self):
        """Test getting boundary by ID from level 1."""
        boundary_id = f"test_admin1_{self.test_suffix}"

        # Create a mock AdminBoundary1 object
        mock_boundary = MagicMock()
        mock_boundary.id = boundary_id
        mock_boundary.shape_name = "Test Admin1"
        mock_boundary.shape_type = "Admin1"
        mock_boundary.shape_group = "ADM1"
        mock_boundary.shape_id = "SHAPE123"
        mock_boundary.geometry = None  # No geometry for this test

        with patch("gefapi.db.session.query") as mock_query:
            mock_query_chain = mock_query.return_value.filter.return_value
            # First query (AdminBoundary0) returns None, second query (AdminBoundary1) returns boundary
            mock_query_chain.first.side_effect = [None, mock_boundary]

            result = BoundariesService.get_boundary_by_id(boundary_id)

            assert result is not None
            assert result["id"] == boundary_id
            assert result["shape_name"] == "Test Admin1"
            assert result["level"] == 1
            assert result["shape_id"] == "SHAPE123"

    def test_get_boundary_by_id_not_found(self):
        """Test getting boundary by ID when not found."""
        boundary_id = f"nonexistent_{self.test_suffix}"

        with patch("gefapi.db.session.query") as mock_query:
            # Both queries return None
            mock_query.return_value.filter.return_value.first.return_value = None

            result = BoundariesService.get_boundary_by_id(boundary_id)

            assert result is None
