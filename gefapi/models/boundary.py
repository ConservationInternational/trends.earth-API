"""BOUNDARY MODELS"""

import datetime

from gefapi import db


class AdminBoundary0Metadata(db.Model):
    """Administrative Boundary Level 0 (Countries) - GeoBoundaries API Metadata

    This model stores the complete API response metadata for ADM0 boundaries.
    The database column names exactly match the geoBoundaries API response fields.

    API Documentation: https://www.geoboundaries.org/api.html
    API Endpoint: https://www.geoboundaries.org/api/current/gbOpen/[ISO]/ADM0/
    """

    __tablename__ = "admin_boundary_0_metadata"

    # Composite primary key: boundaryISO + releaseType
    boundaryISO = db.Column(db.String(10), primary_key=True)
    releaseType = db.Column(
        db.String(20), primary_key=True
    )  # gbOpen, gbHumanitarian, gbAuthoritative

    # Core identification fields matching geoBoundaries API
    boundaryID = db.Column(db.String(100))
    boundaryName = db.Column(db.String(255))
    boundaryType = db.Column(db.String(10))  # "ADM0"
    boundaryCanonical = db.Column(db.String(255))
    boundaryYearRepresented = db.Column(
        db.String(50)
    )  # Year or range like "2021" or "1995 to 2021"

    # Data source and licensing fields
    boundarySource = db.Column(db.Text)  # Maps to 'boundarySource-1' from API
    boundaryLicense = db.Column(db.Text)
    licenseDetail = db.Column(db.Text)
    licenseSource = db.Column(db.Text)
    sourceDataUpdateDate = db.Column(db.String(100))
    buildDate = db.Column(db.String(100))

    # Geographic and political metadata
    Continent = db.Column(db.String(100))
    UNSDG_region = db.Column(db.String(100))
    UNSDG_subregion = db.Column(db.String(100))
    worldBankIncomeGroup = db.Column(db.String(100))

    # Geometry statistics from API (for ADM0 boundaries)
    admUnitCount = db.Column(db.Integer)  # Should be 1 for ADM0
    meanVertices = db.Column(db.Float)
    minVertices = db.Column(db.Integer)
    maxVertices = db.Column(db.Integer)
    meanPerimeterLengthKM = db.Column(db.Float)
    minPerimeterLengthKM = db.Column(db.Float)
    maxPerimeterLengthKM = db.Column(db.Float)
    meanAreaSqKM = db.Column(db.Float)
    minAreaSqKM = db.Column(db.Float)
    maxAreaSqKM = db.Column(db.Float)

    # Download URLs from API
    staticDownloadLink = db.Column(db.Text)
    gjDownloadURL = db.Column(db.Text)  # GeoJSON download URL
    tjDownloadURL = db.Column(db.Text)  # TopoJSON download URL
    simplifiedGeometryGeoJSON = db.Column(db.Text)
    imagePreview = db.Column(db.Text)

    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)

    def __repr__(self):
        return (
            f"<AdminBoundary0Metadata(iso='{self.boundaryISO}', "
            f"release='{self.releaseType}', name='{self.boundaryName}')>"
        )

    def to_dict(self):
        """Convert to dictionary for API response."""
        return {
            "boundaryISO": self.boundaryISO,
            "releaseType": self.releaseType,
            "boundaryID": self.boundaryID,
            "boundaryName": self.boundaryName,
            "boundaryType": self.boundaryType,
            "boundaryCanonical": self.boundaryCanonical,
            "boundaryYearRepresented": self.boundaryYearRepresented,
            "Continent": self.Continent,
            "buildDate": self.buildDate,
            "gjDownloadURL": self.gjDownloadURL,
            "staticDownloadLink": self.staticDownloadLink,
            "tjDownloadURL": self.tjDownloadURL,
            "simplifiedGeometryGeoJSON": self.simplifiedGeometryGeoJSON,
            "imagePreview": self.imagePreview,
            "boundarySource": self.boundarySource,
            "boundaryLicense": self.boundaryLicense,
            "licenseDetail": self.licenseDetail,
            "licenseSource": self.licenseSource,
            "sourceDataUpdateDate": self.sourceDataUpdateDate,
            "UNSDG_region": self.UNSDG_region,
            "UNSDG_subregion": self.UNSDG_subregion,
            "worldBankIncomeGroup": self.worldBankIncomeGroup,
            "admUnitCount": self.admUnitCount,
            "meanVertices": self.meanVertices,
            "minVertices": self.minVertices,
            "maxVertices": self.maxVertices,
            "meanPerimeterLengthKM": self.meanPerimeterLengthKM,
            "minPerimeterLengthKM": self.minPerimeterLengthKM,
            "maxPerimeterLengthKM": self.maxPerimeterLengthKM,
            "meanAreaSqKM": self.meanAreaSqKM,
            "minAreaSqKM": self.minAreaSqKM,
            "maxAreaSqKM": self.maxAreaSqKM,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


class AdminBoundary1Metadata(db.Model):
    """Administrative Boundary Level 1 (States/Provinces) - GeoBoundaries API Metadata

    This model stores the complete API response metadata for ADM1 boundaries.
    The database column names exactly match the geoBoundaries API response fields.

    API Documentation: https://www.geoboundaries.org/api.html
    API Endpoint: https://www.geoboundaries.org/api/current/gbOpen/[ISO]/ADM1/
    """

    __tablename__ = "admin_boundary_1_metadata"

    # Composite primary key: boundaryISO + releaseType
    boundaryISO = db.Column(db.String(10), primary_key=True)
    releaseType = db.Column(
        db.String(20), primary_key=True
    )  # gbOpen, gbHumanitarian, gbAuthoritative

    # Core identification fields matching geoBoundaries API
    boundaryID = db.Column(db.String(100))
    boundaryName = db.Column(db.String(255))  # Country name from API
    boundaryType = db.Column(db.String(10))  # "ADM1"
    boundaryCanonical = db.Column(db.String(255))
    boundaryYearRepresented = db.Column(
        db.String(50)
    )  # Year or range like "2021" or "1995 to 2021"

    # Data source and licensing fields
    boundarySource = db.Column(db.Text)  # Maps to 'boundarySource-1' from API
    boundaryLicense = db.Column(db.Text)
    licenseDetail = db.Column(db.Text)
    licenseSource = db.Column(db.Text)
    sourceDataUpdateDate = db.Column(db.String(100))
    buildDate = db.Column(db.String(100))

    # Geographic and political metadata
    Continent = db.Column(db.String(100))
    UNSDG_region = db.Column(db.String(100))
    UNSDG_subregion = db.Column(db.String(100))
    worldBankIncomeGroup = db.Column(db.String(100))

    # Geometry statistics from API (for all ADM1 units in country)
    admUnitCount = db.Column(db.Integer)  # Count of ADM1 units in this country
    meanVertices = db.Column(db.Float)
    minVertices = db.Column(db.Integer)
    maxVertices = db.Column(db.Integer)
    meanPerimeterLengthKM = db.Column(db.Float)
    minPerimeterLengthKM = db.Column(db.Float)
    maxPerimeterLengthKM = db.Column(db.Float)
    meanAreaSqKM = db.Column(db.Float)
    minAreaSqKM = db.Column(db.Float)
    maxAreaSqKM = db.Column(db.Float)

    # Download URLs from API
    staticDownloadLink = db.Column(db.Text)
    gjDownloadURL = db.Column(db.Text)  # GeoJSON download URL for all ADM1 units
    tjDownloadURL = db.Column(db.Text)  # TopoJSON download URL for all ADM1 units
    simplifiedGeometryGeoJSON = db.Column(db.Text)
    imagePreview = db.Column(db.Text)

    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)

    def __repr__(self):
        return (
            f"<AdminBoundary1Metadata(iso='{self.boundaryISO}', "
            f"release='{self.releaseType}', name='{self.boundaryName}')>"
        )

    def to_dict(self):
        """Convert to dictionary for API response."""
        return {
            "boundaryISO": self.boundaryISO,
            "releaseType": self.releaseType,
            "boundaryID": self.boundaryID,
            "boundaryName": self.boundaryName,
            "boundaryType": self.boundaryType,
            "boundaryCanonical": self.boundaryCanonical,
            "boundaryYearRepresented": self.boundaryYearRepresented,
            "Continent": self.Continent,
            "buildDate": self.buildDate,
            "gjDownloadURL": self.gjDownloadURL,
            "staticDownloadLink": self.staticDownloadLink,
            "tjDownloadURL": self.tjDownloadURL,
            "simplifiedGeometryGeoJSON": self.simplifiedGeometryGeoJSON,
            "imagePreview": self.imagePreview,
            "boundarySource": self.boundarySource,
            "boundaryLicense": self.boundaryLicense,
            "licenseDetail": self.licenseDetail,
            "licenseSource": self.licenseSource,
            "sourceDataUpdateDate": self.sourceDataUpdateDate,
            "UNSDG_region": self.UNSDG_region,
            "UNSDG_subregion": self.UNSDG_subregion,
            "worldBankIncomeGroup": self.worldBankIncomeGroup,
            "admUnitCount": self.admUnitCount,
            "meanVertices": self.meanVertices,
            "minVertices": self.minVertices,
            "maxVertices": self.maxVertices,
            "meanPerimeterLengthKM": self.meanPerimeterLengthKM,
            "minPerimeterLengthKM": self.minPerimeterLengthKM,
            "maxPerimeterLengthKM": self.maxPerimeterLengthKM,
            "meanAreaSqKM": self.meanAreaSqKM,
            "minAreaSqKM": self.minAreaSqKM,
            "maxAreaSqKM": self.maxAreaSqKM,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


class AdminBoundary1Unit(db.Model):
    """Individual Administrative Boundary Level 1 Units

    This model stores individual ADM1 units extracted from the GeoJSON data.
    Each record represents one state/province with its shapeID and shapeName.

    Data is extracted from the 'properties' of features in the GeoJSON file
    available at the gjDownloadURL in AdminBoundary1Metadata.
    """

    __tablename__ = "admin_boundary_1_unit"

    # Composite primary key: shapeID + releaseType
    shapeID = db.Column(db.String(100), primary_key=True)
    releaseType = db.Column(
        db.String(20), primary_key=True
    )  # gbOpen, gbHumanitarian, gbAuthoritative

    # Foreign key to parent country
    boundaryISO = db.Column(db.String(10), nullable=False)

    # Unit identification from GeoJSON properties
    shapeName = db.Column(db.String(255))  # State/province name from GeoJSON

    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow)

    # Foreign key relationships
    adm0_metadata = db.relationship(
        "AdminBoundary0Metadata",
        foreign_keys=[boundaryISO, releaseType],
        primaryjoin=(
            "and_(AdminBoundary1Unit.boundaryISO == AdminBoundary0Metadata.boundaryISO, "
            "AdminBoundary1Unit.releaseType == AdminBoundary0Metadata.releaseType)"
        ),
        backref="adm1_units",
        uselist=False,
    )

    adm1_metadata = db.relationship(
        "AdminBoundary1Metadata",
        foreign_keys=[boundaryISO, releaseType],
        primaryjoin=(
            "and_(AdminBoundary1Unit.boundaryISO == AdminBoundary1Metadata.boundaryISO, "
            "AdminBoundary1Unit.releaseType == AdminBoundary1Metadata.releaseType)"
        ),
        backref="units",
        uselist=False,
    )

    def __repr__(self):
        return (
            f"<AdminBoundary1Unit(shapeID='{self.shapeID}', release='{self.releaseType}', "
            f"name='{self.shapeName}', country='{self.boundaryISO}')>"
        )

    def to_dict(self):
        """Convert to dictionary for API response."""
        return {
            "shapeID": self.shapeID,
            "releaseType": self.releaseType,
            "boundaryISO": self.boundaryISO,
            "shapeName": self.shapeName,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }