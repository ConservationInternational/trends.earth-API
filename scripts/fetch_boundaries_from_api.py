#!/usr/bin/env python3
"""
GeoBoundaries API Data Fetcher

This script fetches administrative boundary data directly from the GeoBoundaries API
and imports it into the Trends.Earth database.

API Documentation: https://www.geoboundaries.org/api.html
API Endpoint: https://www.geoboundaries.org/api/current/gbOpen/[ISO]/[ADM-LEVEL]/

Key Features:
- Fetches ADM0 (country) and ADM1 (state/province) boundaries via REST API
- Downloads TopoJSON geometries for efficient storage
- Stores complete metadata from GeoBoundaries API responses
- Supports batch processing and progress tracking
- Handles API rate limiting and network errors gracefully

SECURITY NOTICE: This tool requires ADMIN or SUPERADMIN privileges for database
write operations.

Usage examples:

# Fetch all ADM0 boundaries (all countries)
python fetch_boundaries_from_api.py fetch-adm0

# Fetch all ADM1 boundaries (all countries)
python fetch_boundaries_from_api.py fetch-adm1

# Fetch both ADM0 and ADM1 boundaries
python fetch_boundaries_from_api.py fetch-all

# Fetch specific country (using ISO 3-letter code)
python fetch_boundaries_from_api.py fetch-country USA

# Show statistics
python fetch_boundaries_from_api.py stats

# Validate data integrity
python fetch_boundaries_from_api.py validate

# Clear all boundary data
python fetch_boundaries_from_api.py clear-all
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import Any
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Add the parent directory to the Python path to import gefapi
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gefapi import app, db
from gefapi.models.boundary import AdminBoundary0, AdminBoundary1

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# GeoBoundaries API configuration
GEOBOUNDARIES_API_BASE = "https://www.geoboundaries.org/api/current/"
RELEASE_TYPE = "gbOpen"  # Can be: gbOpen, gbHumanitarian, gbAuthoritative
API_TIMEOUT = 30  # seconds
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2


class GeoBoundariesAPIClient:
    """Client for interacting with the GeoBoundaries API."""

    def __init__(self, release_type: str = RELEASE_TYPE):
        """Initialize the API client with retry logic.

        Args:
            release_type: Type of release (gbOpen, gbHumanitarian, gbAuthoritative)
        """
        self.base_url = GEOBOUNDARIES_API_BASE
        self.release_type = release_type
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create a requests session with retry logic."""
        session = requests.Session()

        # Configure retry strategy
        retry_strategy = Retry(
            total=RETRY_ATTEMPTS,
            backoff_factor=RETRY_BACKOFF,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        return session

    def get_boundary_metadata(
        self, iso_code: str, adm_level: str
    ) -> dict[str, Any] | None:
        """Fetch boundary metadata from the API.

        Args:
            iso_code: ISO 3-letter country code (e.g., 'USA', 'GBR')
            adm_level: Administrative level ('ADM0' or 'ADM1')

        Returns:
            Dictionary containing boundary metadata, or None if request fails
        """
        url = urljoin(self.base_url, f"{self.release_type}/{iso_code}/{adm_level}/")

        try:
            logger.debug(f"Fetching metadata from: {url}")
            response = self.session.get(url, timeout=API_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"No {adm_level} boundary found for {iso_code}")
                return None
            logger.error(f"HTTP error fetching {iso_code} {adm_level}: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error fetching {iso_code} {adm_level}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response for {iso_code} {adm_level}: {e}")
            return None

    def get_all_countries(self) -> list[dict[str, Any]]:
        """Fetch metadata for all countries (ADM0 level with 'ALL' parameter).

        Returns:
            List of dictionaries containing boundary metadata for all countries
        """
        url = urljoin(self.base_url, f"{self.release_type}/ALL/ADM0/")

        try:
            logger.info(f"Fetching all countries from: {url}")
            response = self.session.get(url, timeout=API_TIMEOUT * 2)
            response.raise_for_status()
            data = response.json()

            # The API returns a list when using 'ALL'
            if isinstance(data, list):
                return data
            # Sometimes it might return a dict with a list inside
            if isinstance(data, dict) and "boundaries" in data:
                return data["boundaries"]
            logger.warning(f"Unexpected API response format: {type(data)}")
            return [data] if isinstance(data, dict) else []

        except requests.exceptions.RequestException as e:
            logger.error(f"Request error fetching all countries: {e}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response for all countries: {e}")
            return []

    def download_topojson(self, url: str) -> dict[str, Any] | None:
        """Download TopoJSON data from a URL.

        Args:
            url: URL to the TopoJSON file

        Returns:
            TopoJSON data as dictionary, or None if download fails
        """
        try:
            logger.debug(f"Downloading TopoJSON from: {url}")
            response = self.session.get(url, timeout=API_TIMEOUT * 3)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error downloading TopoJSON from {url}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid TopoJSON from {url}: {e}")
            return None

    def download_geojson(self, url: str) -> dict[str, Any] | None:
        """Download GeoJSON data from a URL.

        Args:
            url: URL to the GeoJSON file

        Returns:
            GeoJSON data as dictionary, or None if download fails
        """
        try:
            logger.debug(f"Downloading GeoJSON from: {url}")
            response = self.session.get(url, timeout=API_TIMEOUT * 3)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error downloading GeoJSON from {url}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid GeoJSON from {url}: {e}")
            return None


class BoundaryFetcher:
    """Handles fetching and importing boundary data from GeoBoundaries API."""

    def __init__(self, release_type: str = RELEASE_TYPE):
        """Initialize the fetcher.

        Args:
            release_type: Type of release (gbOpen, gbHumanitarian, gbAuthoritative)
        """
        self.api_client = GeoBoundariesAPIClient(release_type)
        self.stats = {
            "adm0_imported": 0,
            "adm0_updated": 0,
            "adm0_errors": 0,
            "adm1_imported": 0,
            "adm1_updated": 0,
            "adm1_errors": 0,
        }

    def _parse_api_response_to_adm0(
        self, api_data: dict[str, Any], topojson_data: dict[str, Any] | None = None
    ) -> AdminBoundary0:
        """Parse API response and create/update AdminBoundary0 object.

        Args:
            api_data: Metadata from GeoBoundaries API
            topojson_data: Optional TopoJSON data for geometry

        Returns:
            AdminBoundary0 object (not yet added to session)
        """
        from geoalchemy2 import WKTElement
        from shapely.geometry import shape
        from shapely.wkt import dumps as wkt_dumps

        iso_code = api_data.get("boundaryISO")

        # Check if record exists
        existing = AdminBoundary0.query.filter_by(id=iso_code).first()

        if existing:
            boundary = existing
        else:
            boundary = AdminBoundary0(id=iso_code)

        # Update fields from API response
        boundary.boundary_id = api_data.get("boundaryID")
        boundary.boundary_name = api_data.get("boundaryName")
        boundary.boundary_iso = api_data.get("boundaryISO")
        boundary.boundary_type = api_data.get("boundaryType")
        boundary.boundary_canonical = api_data.get("boundaryCanonical")
        boundary.boundary_source = api_data.get("boundarySource-1", "")
        boundary.boundary_license = api_data.get("boundaryLicense")
        boundary.license_detail = api_data.get("licenseDetail")
        boundary.license_source = api_data.get("licenseSource")
        boundary.source_data_update_date = api_data.get("sourceDataUpdateDate")
        boundary.build_date = api_data.get("buildDate")

        # Geographic metadata
        boundary.continent = api_data.get("Continent")
        boundary.unsdg_region = api_data.get("UNSDG-region")
        boundary.unsdg_subregion = api_data.get("UNSDG-subregion")
        boundary.world_bank_income_group = api_data.get("worldBankIncomeGroup")

        # Geometry statistics
        try:
            boundary.adm_unit_count = int(api_data.get("admUnitCount", 0))
            boundary.mean_vertices = float(api_data.get("meanVertices", 0))
            boundary.min_vertices = int(api_data.get("minVertices", 0))
            boundary.max_vertices = int(api_data.get("maxVertices", 0))
            boundary.mean_perimeter_km = float(api_data.get("meanPerimeterLengthKM", 0))
            boundary.min_perimeter_km = float(api_data.get("minPerimeterLengthKM", 0))
            boundary.max_perimeter_km = float(api_data.get("maxPerimeterLengthKM", 0))
            boundary.mean_area_sqkm = float(api_data.get("meanAreaSqKM", 0))
            boundary.min_area_sqkm = float(api_data.get("minAreaSqKM", 0))
            boundary.max_area_sqkm = float(api_data.get("maxAreaSqKM", 0))
        except (ValueError, TypeError) as e:
            logger.warning(f"Error parsing statistics for {iso_code}: {e}")

        # Download URLs
        boundary.static_download_link = api_data.get("staticDownloadLink")
        boundary.geojson_download_url = api_data.get("gjDownloadURL")
        boundary.topojson_download_url = api_data.get("tjDownloadURL")
        boundary.simplified_geojson_url = api_data.get("simplifiedGeometryGeoJSON")
        boundary.image_preview_url = api_data.get("imagePreview")

        # Process geometry if TopoJSON data provided
        if topojson_data:
            try:
                # Convert TopoJSON to GeoJSON
                import topojson

                # TopoJSON client library converts to GeoJSON feature collection
                geojson_data = topojson.Topology(topojson_data).to_geojson()

                # Combine all features into a single geometry
                geometries = []
                if "features" in geojson_data:
                    for feature in geojson_data["features"]:
                        if "geometry" in feature:
                            geom = shape(feature["geometry"])
                            geometries.append(geom)

                if geometries:
                    # Convert to WKT for PostGIS
                    from shapely.ops import unary_union

                    combined_geom = (
                        unary_union(geometries)
                        if len(geometries) > 1
                        else geometries[0]
                    )
                    wkt = wkt_dumps(combined_geom)
                    boundary.geometry = WKTElement(wkt, srid=4326)
            except Exception as e:
                logger.error(f"Error processing TopoJSON geometry for {iso_code}: {e}")

        return boundary

    def _parse_api_response_to_adm1(
        self, api_data: dict[str, Any], topojson_data: dict[str, Any] | None = None
    ) -> list[AdminBoundary1]:
        """Parse API response and create/update AdminBoundary1 objects.

        Args:
            api_data: Metadata from GeoBoundaries API
            topojson_data: Optional TopoJSON data for geometries

        Returns:
            List of AdminBoundary1 objects (not yet added to session)
        """
        from geoalchemy2 import WKTElement
        from shapely.geometry import shape
        from shapely.wkt import dumps as wkt_dumps

        iso_code = api_data.get("boundaryISO")
        boundaries = []

        if not topojson_data:
            logger.warning(f"No TopoJSON data for {iso_code} ADM1")
            return boundaries

        # Convert TopoJSON to GeoJSON
        try:
            import topojson

            geojson_data = topojson.Topology(topojson_data).to_geojson()
        except Exception as e:
            logger.error(f"Error converting TopoJSON to GeoJSON for {iso_code}: {e}")
            return boundaries

        if "features" not in geojson_data:
            logger.warning(f"No features in converted GeoJSON for {iso_code} ADM1")
            return boundaries

        # Process each feature in the GeoJSON
        for feature in geojson_data["features"]:
            properties = feature.get("properties", {})

            # Get shapeID from properties (may have different field names)
            shape_id = (
                properties.get("shapeID")
                or properties.get("shapeid")
                or properties.get("shapeId")
                or f"{iso_code}-ADM1-{len(boundaries)}"
            )

            # Check if record exists
            existing = AdminBoundary1.query.filter_by(shape_id=shape_id).first()

            if existing:
                boundary = existing
            else:
                boundary = AdminBoundary1(shape_id=shape_id)

            # Update fields from API response
            boundary.id = iso_code  # Country code
            boundary.boundary_id = api_data.get("boundaryID")
            boundary.boundary_name = api_data.get("boundaryName")
            boundary.boundary_iso = iso_code
            boundary.boundary_type = "ADM1"
            boundary.boundary_canonical = api_data.get("boundaryCanonical")
            boundary.boundary_source = api_data.get("boundarySource-1", "")
            boundary.boundary_license = api_data.get("boundaryLicense")
            boundary.license_detail = api_data.get("licenseDetail")
            boundary.license_source = api_data.get("licenseSource")
            boundary.source_data_update_date = api_data.get("sourceDataUpdateDate")
            boundary.build_date = api_data.get("buildDate")

            # Geographic metadata (country-level)
            boundary.continent = api_data.get("Continent")
            boundary.unsdg_region = api_data.get("UNSDG-region")
            boundary.unsdg_subregion = api_data.get("UNSDG-subregion")
            boundary.world_bank_income_group = api_data.get("worldBankIncomeGroup")

            # Geometry statistics (use country-level stats)
            try:
                boundary.adm_unit_count = int(api_data.get("admUnitCount", 0))
                boundary.mean_vertices = float(api_data.get("meanVertices", 0))
                boundary.min_vertices = int(api_data.get("minVertices", 0))
                boundary.max_vertices = int(api_data.get("maxVertices", 0))
                boundary.mean_perimeter_km = float(
                    api_data.get("meanPerimeterLengthKM", 0)
                )
                boundary.min_perimeter_km = float(
                    api_data.get("minPerimeterLengthKM", 0)
                )
                boundary.max_perimeter_km = float(
                    api_data.get("maxPerimeterLengthKM", 0)
                )
                boundary.mean_area_sqkm = float(api_data.get("meanAreaSqKM", 0))
                boundary.min_area_sqkm = float(api_data.get("minAreaSqKM", 0))
                boundary.max_area_sqkm = float(api_data.get("maxAreaSqKM", 0))
            except (ValueError, TypeError) as e:
                logger.warning(f"Error parsing statistics for {shape_id}: {e}")

            # Download URLs
            boundary.static_download_link = api_data.get("staticDownloadLink")
            boundary.geojson_download_url = api_data.get("gjDownloadURL")
            boundary.topojson_download_url = api_data.get("tjDownloadURL")
            boundary.simplified_geojson_url = api_data.get("simplifiedGeometryGeoJSON")
            boundary.image_preview_url = api_data.get("imagePreview")

            # Process geometry from feature
            if "geometry" in feature:
                try:
                    geom = shape(feature["geometry"])
                    wkt = wkt_dumps(geom)
                    boundary.geometry = WKTElement(wkt, srid=4326)
                except Exception as e:
                    logger.error(f"Error processing geometry for {shape_id}: {e}")

            boundaries.append(boundary)

        return boundaries

    def fetch_country_adm0(self, iso_code: str) -> bool:
        """Fetch and import ADM0 boundary for a specific country.

        Args:
            iso_code: ISO 3-letter country code

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Fetching ADM0 boundary for {iso_code}")

        # Get metadata from API
        api_data = self.api_client.get_boundary_metadata(iso_code, "ADM0")
        if not api_data:
            self.stats["adm0_errors"] += 1
            return False

        # Download TopoJSON for geometry
        topojson_url = api_data.get("tjDownloadURL")
        topojson_data = None
        if topojson_url:
            topojson_data = self.api_client.download_topojson(topojson_url)

        try:
            # Parse and save to database
            boundary = self._parse_api_response_to_adm0(api_data, topojson_data)

            # Check if this was an update or new insert
            if boundary.id and AdminBoundary0.query.filter_by(id=boundary.id).first():
                self.stats["adm0_updated"] += 1
                logger.debug(f"Updated ADM0: {iso_code}")
            else:
                db.session.add(boundary)
                self.stats["adm0_imported"] += 1
                logger.debug(f"Imported ADM0: {iso_code}")

            db.session.commit()
            return True

        except Exception as e:
            logger.error(f"Error saving ADM0 for {iso_code}: {e}")
            db.session.rollback()
            self.stats["adm0_errors"] += 1
            return False

    def fetch_country_adm1(self, iso_code: str) -> bool:
        """Fetch and import ADM1 boundaries for a specific country.

        Args:
            iso_code: ISO 3-letter country code

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Fetching ADM1 boundaries for {iso_code}")

        # Get metadata from API
        api_data = self.api_client.get_boundary_metadata(iso_code, "ADM1")
        if not api_data:
            # Not all countries have ADM1 data
            logger.info(f"No ADM1 data available for {iso_code}")
            return True

        # Download TopoJSON for geometries
        topojson_url = api_data.get("tjDownloadURL")
        topojson_data = None
        if topojson_url:
            topojson_data = self.api_client.download_topojson(topojson_url)

        if not topojson_data:
            logger.warning(f"Could not download TopoJSON for {iso_code} ADM1")
            self.stats["adm1_errors"] += 1
            return False

        try:
            # Parse and save to database
            boundaries = self._parse_api_response_to_adm1(api_data, topojson_data)

            for boundary in boundaries:
                if (
                    boundary.shape_id
                    and AdminBoundary1.query.filter_by(
                        shape_id=boundary.shape_id
                    ).first()
                ):
                    self.stats["adm1_updated"] += 1
                else:
                    db.session.add(boundary)
                    self.stats["adm1_imported"] += 1

            db.session.commit()
            logger.info(f"Imported {len(boundaries)} ADM1 boundaries for {iso_code}")
            return True

        except Exception as e:
            logger.error(f"Error saving ADM1 for {iso_code}: {e}")
            db.session.rollback()
            self.stats["adm1_errors"] += 1
            return False

    def fetch_all_adm0(self) -> None:
        """Fetch ADM0 boundaries for all countries."""
        logger.info("Fetching all ADM0 boundaries from GeoBoundaries API")

        # Get list of all countries
        all_countries = self.api_client.get_all_countries()

        if not all_countries:
            logger.error("Could not retrieve country list from API")
            return

        logger.info(f"Found {len(all_countries)} countries to process")

        # Process each country
        for i, country_data in enumerate(all_countries, 1):
            iso_code = country_data.get("boundaryISO")
            if not iso_code:
                logger.warning(f"Skipping country without ISO code: {country_data}")
                continue

            logger.info(f"[{i}/{len(all_countries)}] Processing {iso_code}")

            # We already have the metadata, so we can optimize by not re-fetching
            topojson_url = country_data.get("tjDownloadURL")
            topojson_data = None
            if topojson_url:
                topojson_data = self.api_client.download_topojson(topojson_url)

            try:
                boundary = self._parse_api_response_to_adm0(country_data, topojson_data)

                if (
                    boundary.id
                    and AdminBoundary0.query.filter_by(id=boundary.id).first()
                ):
                    self.stats["adm0_updated"] += 1
                else:
                    db.session.add(boundary)
                    self.stats["adm0_imported"] += 1

                # Commit every 10 countries
                if i % 10 == 0:
                    db.session.commit()
                    logger.info(
                        f"Progress: {i}/{len(all_countries)} countries processed"
                    )

                # Rate limiting - be nice to the API
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"Error processing {iso_code}: {e}")
                db.session.rollback()
                self.stats["adm0_errors"] += 1
                continue

        # Final commit
        db.session.commit()
        logger.info(
            f"ADM0 fetch completed. Imported: {self.stats['adm0_imported']}, "
            f"Updated: {self.stats['adm0_updated']}, "
            f"Errors: {self.stats['adm0_errors']}"
        )

    def fetch_all_adm1(self) -> None:
        """Fetch ADM1 boundaries for all countries."""
        logger.info("Fetching all ADM1 boundaries from GeoBoundaries API")

        # Get list of all countries from database (ADM0 should be populated first)
        countries = AdminBoundary0.query.all()

        if not countries:
            logger.error(
                "No countries found in database. Please fetch ADM0 data first."
            )
            return

        logger.info(f"Found {len(countries)} countries to process for ADM1")

        # Process each country
        for i, country in enumerate(countries, 1):
            iso_code = country.id
            logger.info(
                f"[{i}/{len(countries)}] Processing ADM1 for {iso_code} "
                f"({country.boundary_name or iso_code})"
            )

            success = self.fetch_country_adm1(iso_code)

            if success and (i % 10 == 0):
                logger.info(f"Progress: {i}/{len(countries)} countries processed")

            # Rate limiting - be nice to the API
            time.sleep(1)

        logger.info(
            f"ADM1 fetch completed. Imported: {self.stats['adm1_imported']}, "
            f"Updated: {self.stats['adm1_updated']}, "
            f"Errors: {self.stats['adm1_errors']}"
        )

    def clear_all_boundaries(self):
        """Clear all existing boundary data from the database."""
        logger.warning("Clearing all boundary data...")

        # Delete all ADM1 first (foreign key constraint)
        adm1_count = AdminBoundary1.query.count()
        AdminBoundary1.query.delete()

        # Delete all ADM0
        adm0_count = AdminBoundary0.query.count()
        AdminBoundary0.query.delete()

        db.session.commit()
        logger.info(f"Cleared {adm0_count} ADM0 and {adm1_count} ADM1 records")

    def show_stats(self):
        """Display statistics about current boundary data."""
        adm0_count = AdminBoundary0.query.count()
        adm1_count = AdminBoundary1.query.count()

        logger.info("=== Boundary Data Statistics ===")
        logger.info(f"ADM0 (Countries): {adm0_count} records")
        logger.info(f"ADM1 (States/Provinces): {adm1_count} records")

        if adm0_count > 0:
            # Show sample countries
            sample_countries = AdminBoundary0.query.limit(5).all()
            logger.info("Sample countries:")
            for country in sample_countries:
                logger.info(f"  - {country.id}: {country.boundary_name or country.id}")

        if adm1_count > 0:
            # Show distribution by country
            from sqlalchemy import func

            country_counts = (
                db.session.query(
                    AdminBoundary1.id,
                    func.count(AdminBoundary1.shape_id).label("count"),
                )
                .group_by(AdminBoundary1.id)
                .order_by(func.count(AdminBoundary1.shape_id).desc())
                .limit(10)
                .all()
            )

            logger.info("Top 10 countries by ADM1 count:")
            for country_id, count in country_counts:
                country = AdminBoundary0.query.filter_by(id=country_id).first()
                country_name = (
                    country.boundary_name if country else country_id or "Unknown"
                )
                logger.info(f"  - {country_id} ({country_name}): {count} admin1 units")

    def validate_data(self):
        """Validate the integrity of imported boundary data."""
        logger.info("=== Data Validation ===")

        # Check for ADM1 records with invalid country references
        orphaned_adm1 = (
            db.session.query(AdminBoundary1)
            .filter(~AdminBoundary1.id.in_(db.session.query(AdminBoundary0.id)))
            .all()
        )

        if orphaned_adm1:
            logger.warning(
                f"Found {len(orphaned_adm1)} ADM1 records with invalid country "
                f"references:"
            )
            for adm1 in orphaned_adm1[:10]:  # Show first 10
                logger.warning(f"  - {adm1.shape_id}: country '{adm1.id}' not found")
        else:
            logger.info("All ADM1 records have valid country references")

        # Check for missing geometries
        adm0_no_geom = AdminBoundary0.query.filter(
            AdminBoundary0.geometry.is_(None)
        ).count()
        adm1_no_geom = AdminBoundary1.query.filter(
            AdminBoundary1.geometry.is_(None)
        ).count()

        if adm0_no_geom > 0:
            logger.warning(f"Found {adm0_no_geom} ADM0 records without geometry")
        if adm1_no_geom > 0:
            logger.warning(f"Found {adm1_no_geom} ADM1 records without geometry")

        if adm0_no_geom == 0 and adm1_no_geom == 0:
            logger.info("All boundary records have geometry data")

        logger.info("Validation completed")


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Fetch geoBoundaries data from API and import into database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Fetch ADM0 command
    subparsers.add_parser("fetch-adm0", help="Fetch all ADM0 (country) boundaries")

    # Fetch ADM1 command
    subparsers.add_parser(
        "fetch-adm1", help="Fetch all ADM1 (state/province) boundaries"
    )

    # Fetch all command
    subparsers.add_parser("fetch-all", help="Fetch both ADM0 and ADM1 boundaries")

    # Fetch specific country
    fetch_country_parser = subparsers.add_parser(
        "fetch-country", help="Fetch boundaries for a specific country"
    )
    fetch_country_parser.add_argument(
        "iso_code", help="ISO 3-letter country code (e.g., USA, GBR, DEU)"
    )
    fetch_country_parser.add_argument(
        "--adm-level",
        choices=["ADM0", "ADM1", "both"],
        default="both",
        help="Administrative level to fetch (default: both)",
    )

    # Clear all command
    subparsers.add_parser(
        "clear-all", help="Clear all boundary data (use with caution)"
    )

    # Stats command
    subparsers.add_parser("stats", help="Show boundary data statistics")

    # Validate command
    subparsers.add_parser("validate", help="Validate boundary data integrity")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Initialize Flask app context
    with app.app_context():
        fetcher = BoundaryFetcher()

        try:
            if args.command == "fetch-adm0":
                fetcher.fetch_all_adm0()

            elif args.command == "fetch-adm1":
                fetcher.fetch_all_adm1()

            elif args.command == "fetch-all":
                logger.info("Fetching all boundaries (ADM0 + ADM1)")
                fetcher.fetch_all_adm0()
                fetcher.fetch_all_adm1()

            elif args.command == "fetch-country":
                iso_code = args.iso_code.upper()

                if args.adm_level in ("ADM0", "both"):
                    fetcher.fetch_country_adm0(iso_code)

                if args.adm_level in ("ADM1", "both"):
                    fetcher.fetch_country_adm1(iso_code)

            elif args.command == "clear-all":
                response = input(
                    "Are you sure you want to clear all boundary data? "
                    "Type 'yes' to confirm: "
                )
                if response.lower() == "yes":
                    fetcher.clear_all_boundaries()
                else:
                    logger.info("Operation cancelled")

            elif args.command == "stats":
                fetcher.show_stats()

            elif args.command == "validate":
                fetcher.validate_data()

        except KeyboardInterrupt:
            logger.warning("Operation interrupted by user")
            return 1
        except Exception as e:
            logger.error(f"Error: {str(e)}", exc_info=True)
            return 1

        # Show final stats if fetch was performed
        if args.command.startswith("fetch"):
            fetcher.show_stats()

    return 0


if __name__ == "__main__":
    sys.exit(main())
