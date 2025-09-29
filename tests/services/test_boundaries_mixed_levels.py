"""
Tests for mixed-level boundaries and timestamp filtering functionality.
"""

from datetime import datetime, timedelta

from geoalchemy2 import WKTElement

from gefapi import db
from gefapi.models.boundary import AdminBoundary0, AdminBoundary1
from gefapi.services.boundaries_service import BoundariesService


class TestBoundariesMixedLevels:
    """Test mixed-level boundary queries and timestamp filtering."""

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
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Generate unique test suffix using current time
        import time

        self.test_suffix = str(int(time.time() * 1000))[-3:]

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
            db.session.commit()
        except Exception:
            db.session.rollback()

    def test_get_boundaries_both(self):
        """Test querying both ADM0 and ADM1 boundaries in one call."""
        # Create test data
        now = datetime.utcnow()

        country = AdminBoundary0(
            id=f"T5{self.test_suffix}",
            shape_name="Test Country Mixed",
            shape_group="Country",
            shape_type="ADM0",
            geometry=WKTElement(
                "POLYGON((-180 -90, -180 90, 180 90, 180 -90, -180 -90))", srid=4326
            ),
            created_at=now,
            updated_at=now,
        )

        state = AdminBoundary1(
            shape_id=f"T5{self.test_suffix}-A1",
            id=f"T5{self.test_suffix}",
            shape_name="Test State Mixed",
            shape_group="State",
            shape_type="ADM1",
            geometry=WKTElement(
                "POLYGON((-125 30, -125 42, -114 42, -114 30, -125 30))", srid=4326
            ),
            created_at=now,
            updated_at=now,
        )

        db.session.add(country)
        db.session.add(state)
        db.session.commit()

        # Query both levels
        results, total = BoundariesService.get_boundaries(
            levels=[0, 1], filters=None, page=1, per_page=100
        )

        assert total >= 2
        assert len(results) >= 2

        # Check that we have results from both levels
        level_0_found = any(r["level"] == 0 for r in results)
        level_1_found = any(r["level"] == 1 for r in results)
        assert level_0_found
        assert level_1_found

        # Verify timestamps are included
        for result in results:
            assert "created_at" in result
            assert "updated_at" in result
            assert result["created_at"] is not None
            assert result["updated_at"] is not None

    def test_get_boundaries_with_timestamp_filtering(self):
        """Test timestamp filtering with created_at_since and updated_at_since."""
        # Create test data with different timestamps
        old_time = datetime.utcnow() - timedelta(days=10)
        recent_time = datetime.utcnow() - timedelta(hours=1)

        old_country = AdminBoundary0(
            id=f"T6{self.test_suffix}",
            shape_name="Old Country",
            shape_group="Country",
            shape_type="ADM0",
            geometry=WKTElement(
                "POLYGON((-180 -90, -180 90, 180 90, 180 -90, -180 -90))", srid=4326
            ),
            created_at=old_time,
            updated_at=old_time,
        )

        new_country = AdminBoundary0(
            id=f"T7{self.test_suffix}",
            shape_name="New Country",
            shape_group="Country",
            shape_type="ADM0",
            geometry=WKTElement(
                "POLYGON((-180 -90, -180 90, 180 90, 180 -90, -180 -90))", srid=4326
            ),
            created_at=recent_time,
            updated_at=recent_time,
        )

        db.session.add(old_country)
        db.session.add(new_country)
        db.session.commit()

        # Query with created_at_since filter
        cutoff_time = datetime.utcnow() - timedelta(hours=2)
        results, total = BoundariesService.get_boundaries(
            levels=[0], filters={"created_at_since": cutoff_time}, page=1, per_page=100
        )

        # Should only get the recent country
        matching_results = [
            r
            for r in results
            if r["id"] in [f"T6{self.test_suffix}", f"T7{self.test_suffix}"]
        ]
        assert len(matching_results) == 1
        assert matching_results[0]["shape_name"] == "New Country"

    def test_mixed_levels_with_timestamp_filtering(self):
        """Test combining mixed levels with timestamp filtering."""
        # Create test data with recent timestamps
        recent_time = datetime.utcnow() - timedelta(minutes=30)

        country = AdminBoundary0(
            id=f"T8{self.test_suffix}",
            shape_name="Recent Country",
            shape_group="Country",
            shape_type="ADM0",
            geometry=WKTElement(
                "POLYGON((-180 -90, -180 90, 180 90, 180 -90, -180 -90))", srid=4326
            ),
            created_at=recent_time,
            updated_at=recent_time,
        )

        state = AdminBoundary1(
            shape_id=f"T8{self.test_suffix}-A1",
            id=f"T8{self.test_suffix}",
            shape_name="Recent State",
            shape_group="State",
            shape_type="ADM1",
            geometry=WKTElement(
                "POLYGON((-125 30, -125 42, -114 42, -114 30, -125 30))", srid=4326
            ),
            created_at=recent_time,
            updated_at=recent_time,
        )

        db.session.add(country)
        db.session.add(state)
        db.session.commit()

        # Query both levels with timestamp filter
        cutoff_time = datetime.utcnow() - timedelta(hours=1)
        results, total = BoundariesService.get_boundaries(
            levels=[0, 1],
            filters={"created_at_since": cutoff_time},
            page=1,
            per_page=100,
        )

        # Should get both the country and state
        matching_results = [
            r
            for r in results
            if r["id"] == f"T8{self.test_suffix}"
            or r.get("shape_id") == f"T8{self.test_suffix}-A1"
        ]
        assert len(matching_results) >= 2

    def test_timestamp_filter_format_validation(self):
        """Test that timestamp filters handle different datetime formats."""
        # This test would be handled by the route validation
        # Here we test the service directly with proper datetime objects
        cutoff_time = datetime.utcnow() - timedelta(days=1)

        # Should not raise an error with proper datetime object
        results, total = BoundariesService.get_boundaries(
            levels=[0], filters={"created_at_since": cutoff_time}, page=1, per_page=10
        )

        # Results should be a list (empty or with data)
        assert isinstance(results, list)
        assert isinstance(total, int)
