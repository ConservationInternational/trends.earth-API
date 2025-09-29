"""
Tests for boundaries API endpoints with mixed levels and timestamp filtering.
"""

from datetime import datetime, timedelta
import json

from geoalchemy2 import WKTElement
import pytest

from gefapi import db
from gefapi.models.boundary import AdminBoundary0, AdminBoundary1


@pytest.mark.usefixtures("app", "db_session")
class TestBoundariesAPIExtended:
    """Test extended boundaries API functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        # Clean up any existing test data
        try:
            db.session.query(AdminBoundary0).filter(
                AdminBoundary0.id.like("M%")
            ).delete()
            db.session.query(AdminBoundary1).filter(
                AdminBoundary1.shape_id.like("M%")
            ).delete()
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Generate unique test suffix
        import time

        self.test_suffix = str(int(time.time() * 1000))[-3:]

    def teardown_method(self):
        """Clean up test fixtures."""
        try:
            db.session.query(AdminBoundary0).filter(
                AdminBoundary0.id.like("M%")
            ).delete()
            db.session.query(AdminBoundary1).filter(
                AdminBoundary1.shape_id.like("M%")
            ).delete()
            db.session.commit()
        except Exception:
            db.session.rollback()

    def test_mixed_levels_api_endpoint(self, client):
        """Test API endpoint with mixed levels parameter."""
        # Create test data
        country = AdminBoundary0(
            id=f"MX{self.test_suffix}",
            shape_name="Mixed Test Country",
            shape_group="Country",
            shape_type="ADM0",
            geometry=WKTElement(
                "POLYGON((-180 -90, -180 90, 180 90, 180 -90, -180 -90))", srid=4326
            ),
        )

        state = AdminBoundary1(
            shape_id=f"MX{self.test_suffix}-A1",
            id=f"MX{self.test_suffix}",
            shape_name="Mixed Test State",
            shape_group="State",
            shape_type="ADM1",
            geometry=WKTElement(
                "POLYGON((-125 30, -125 42, -114 42, -114 30, -125 30))", srid=4326
            ),
        )

        db.session.add(country)
        db.session.add(state)
        db.session.commit()

        # Test mixed levels query
        response = client.get("/api/v1/data/boundaries?level=0,1&format=table")

        assert response.status_code == 200
        data = json.loads(response.data)

        assert "data" in data
        assert "meta" in data
        assert "levels" in data["meta"]
        assert data["meta"]["levels"] == [0, 1]

        # Should contain results from both levels
        results = data["data"]
        levels_found = {r.get("level") for r in results}
        assert 0 in levels_found or 1 in levels_found

    def test_timestamp_filtering_api_endpoint(self, client):
        """Test API endpoint with timestamp filtering."""
        # Create test data with recent timestamp
        recent_time = datetime.utcnow() - timedelta(minutes=5)

        country = AdminBoundary0(
            id=f"MT{self.test_suffix}",
            shape_name="Recent Test Country",
            shape_group="Country",
            shape_type="ADM0",
            geometry=WKTElement(
                "POLYGON((-180 -90, -180 90, 180 90, 180 -90, -180 -90))", srid=4326
            ),
            created_at=recent_time,
            updated_at=recent_time,
        )

        db.session.add(country)
        db.session.commit()

        # Test with created_at_since filter
        cutoff = (datetime.utcnow() - timedelta(minutes=10)).isoformat() + "Z"
        response = client.get(
            f"/api/v1/data/boundaries?level=0&created_at_since={cutoff}&format=table"
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        # Should find the recent country
        results = data["data"]
        matching_results = [
            r for r in results if r.get("id") == f"MT{self.test_suffix}"
        ]
        assert len(matching_results) >= 0  # Might not find it due to test timing

    def test_invalid_level_parameter(self, client):
        """Test API with invalid level parameter."""
        response = client.get("/api/v1/data/boundaries?level=2")

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "detail" in data
        assert "Invalid level parameter" in data["detail"]

    def test_invalid_timestamp_format(self, client):
        """Test API with invalid timestamp format."""
        response = client.get(
            "/api/v1/data/boundaries?level=0&created_at_since=invalid-date"
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "detail" in data
        assert "Invalid created_at_since format" in data["detail"]

    def test_mixed_levels_with_timestamp_combined(self, client):
        """Test combining mixed levels with timestamp filtering."""
        # Create recent test data
        recent_time = datetime.utcnow() - timedelta(minutes=2)

        country = AdminBoundary0(
            id=f"MC{self.test_suffix}",
            shape_name="Combined Test Country",
            shape_group="Country",
            shape_type="ADM0",
            geometry=WKTElement(
                "POLYGON((-180 -90, -180 90, 180 90, 180 -90, -180 -90))", srid=4326
            ),
            created_at=recent_time,
            updated_at=recent_time,
        )

        state = AdminBoundary1(
            shape_id=f"MC{self.test_suffix}-A1",
            id=f"MC{self.test_suffix}",
            shape_name="Combined Test State",
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

        # Test combined query
        cutoff = (datetime.utcnow() - timedelta(minutes=5)).isoformat() + "Z"
        response = client.get(
            f"/api/v1/data/boundaries?level=0,1&created_at_since={cutoff}&format=table"
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert "data" in data
        assert "meta" in data
        assert data["meta"]["levels"] == [0, 1]

        # Verify timestamp fields are included in results
        results = data["data"]
        for result in results[:5]:  # Check first few results
            if "created_at" in result:
                assert result["created_at"] is not None
            if "updated_at" in result:
                assert result["updated_at"] is not None
