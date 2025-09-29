"""BOUNDARY MODELS"""

import datetime

from geoalchemy2 import Geometry

from gefapi import db


class AdminBoundary0(db.Model):
    """Administrative Boundary Level 0 (Countries)"""

    __tablename__ = "admin_boundary_0"

    # Use the 'id' field from geoBoundaries as primary key
    # (ISO 3-letter codes like 'AFG', 'ALB')
    id = db.Column(db.String(10), primary_key=True)
    shape_group = db.Column(db.String(100))
    shape_type = db.Column(db.String(50))
    shape_name = db.Column(db.String(255))

    # PostGIS geometry column for proper spatial operations
    geometry = db.Column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True))

    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)

    def __repr__(self):
        return f"<AdminBoundary0(id='{self.id}', name='{self.shape_name}')>"

    def to_dict(self, include_geometry=False):
        """Convert to dictionary for API response"""
        result = {
            "id": self.id,
            "shapeGroup": self.shape_group,
            "shapeType": self.shape_type,
            "shapeName": self.shape_name,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }
        # Note: Geometry serialization would require special handling if needed
        return result


class AdminBoundary1(db.Model):
    """Administrative Boundary Level 1 (States/Provinces)"""

    __tablename__ = "admin_boundary_1"

    # Use the 'shapeID' field from geoBoundaries as primary key
    # (unique identifiers like 'AFG-ADM1-3_0_0_B1')
    shape_id = db.Column(db.String(100), primary_key=True)
    id = db.Column(db.String(10))  # Country ISO code (e.g., 'AFG')
    shape_name = db.Column(db.String(255))
    shape_group = db.Column(db.String(100))
    shape_type = db.Column(db.String(50))

    # PostGIS geometry column for proper spatial operations
    geometry = db.Column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True))

    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)

    # Foreign key relationship to AdminBoundary0
    country = db.relationship(
        "AdminBoundary0",
        foreign_keys=[id],
        primaryjoin="AdminBoundary1.id == AdminBoundary0.id",
        backref="admin1_boundaries",
        uselist=False,
    )

    def __repr__(self):
        return (
            f"<AdminBoundary1(shape_id='{self.shape_id}', "
            f"name='{self.shape_name}', country='{self.id}')>"
        )

    def to_dict(self, include_geometry=False):
        """Convert to dictionary for API response"""
        result = {
            "shapeId": self.shape_id,
            "id": self.id,
            "shapeName": self.shape_name,
            "shapeGroup": self.shape_group,
            "shapeType": self.shape_type,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }
        # Note: Geometry serialization would require special handling if needed
        return result
