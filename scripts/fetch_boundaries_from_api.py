#!/usr/bin/env python3
"""
GeoBoundaries API Data Fetcher - Three-Table Architecture

This script fetches administrative boundary metadata from the GeoBoundaries API
and stores it in the Trends.Earth database using a three-table structure:

1. AdminBoundary0Metadata - Country-level API metadata
2. AdminBoundary1Metadata - ADM1 API metadata (country-level)
3. AdminBoundary1Unit - Individual state/province units extracted from GeoJSON

API Documentation: https://www.geoboundaries.org/api.html
API Endpoint: https://www.geoboundaries.org/api/current/gbOpen/[ISO]/[ADM-LEVEL]/

Architecture:
- ADM0 Metadata: Stores complete API response for country-level boundaries
- ADM1 Metadata: Stores complete API response including download URLs and statistics
- ADM1 Units: Individual state/province units extracted from simplified GeoJSON

Database Schema:
- AdminBoundary0Metadata: Primary key (boundaryISO, releaseType)
- AdminBoundary1Metadata: Primary key (boundaryISO, releaseType)
- AdminBoundary1Unit: Primary key (shapeID, releaseType), FK to both metadata tables

Key Features:
- Fetches ADM0 and ADM1 metadata via REST API
- Downloads simplified GeoJSON to extract ADM1 unit names
- Stores metadata with exact API field names geom geoBoundaries
- Supports batch processing and progress tracking

Usage examples:

# Fetch all boundaries (all countries, all release types, both ADM0 and ADM1)
python fetch_boundaries_from_api.py fetch-all

# Fetch specific country (all release types, both ADM0 and ADM1)
python fetch_boundaries_from_api.py fetch-country USA

# Show statistics
python fetch_boundaries_from_api.py stats

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
from gefapi.models.boundary import (
    AdminBoundary0Metadata,
    AdminBoundary1Metadata,
    AdminBoundary1Unit,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# GeoBoundaries API configuration
GEOBOUNDARIES_API_BASE = "https://www.geoboundaries.org/api/current/"
RELEASE_TYPES = ["gbOpen", "gbHumanitarian", "gbAuthoritative"]
API_TIMEOUT = 30  # seconds
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2


class GeoBoundariesAPIClient:
    """Client for interacting with the GeoBoundaries API."""

    def __init__(self, release_type: str = "gbOpen"):
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


class BoundaryFetcher:
    """Handles fetching and importing boundary data from GeoBoundaries API.

    Uses three-table architecture:
    - AdminBoundary0Metadata: Country-level API metadata
    - AdminBoundary1Metadata: ADM1 API metadata (country-level)
    - AdminBoundary1Unit: Individual state/province units
    """

    def __init__(self, release_type: str = "gbOpen"):
        """Initialize the fetcher.

        Args:
            release_type: Type of release (gbOpen, gbHumanitarian, gbAuthoritative)
        """
        self.api_client = GeoBoundariesAPIClient(release_type)
        self.stats = {
            "adm0_metadata_created": 0,
            "adm0_metadata_updated": 0,
            "adm1_metadata_created": 0,
            "adm1_metadata_updated": 0,
            "adm1_units_created": 0,
            "adm1_units_updated": 0,
        }

    def _populate_metadata_fields(
        self,
        metadata: AdminBoundary0Metadata | AdminBoundary1Metadata,
        api_data: dict[str, Any],
    ) -> None:
        """Populate metadata fields from API response.

        Args:
            metadata: Metadata object to populate
            api_data: API response data
        """
        # Core identification
        metadata.boundaryID = api_data.get("boundaryID")
        metadata.boundaryName = api_data.get("boundaryName")
        metadata.boundaryType = api_data.get("boundaryType")
        metadata.boundaryCanonical = api_data.get("boundaryCanonical")
        metadata.boundaryYearRepresented = api_data.get("boundaryYearRepresented")

        # Data source and licensing
        metadata.boundarySource = api_data.get("boundarySource-1", "")
        metadata.boundaryLicense = api_data.get("boundaryLicense")
        metadata.licenseDetail = api_data.get("licenseDetail")
        metadata.licenseSource = api_data.get("licenseSource")
        metadata.sourceDataUpdateDate = api_data.get("sourceDataUpdateDate")
        metadata.buildDate = api_data.get("buildDate")

        # Geographic metadata
        metadata.Continent = api_data.get("Continent")
        metadata.UNSDG_region = api_data.get("UNSDG-region")
        metadata.UNSDG_subregion = api_data.get("UNSDG-subregion")
        metadata.worldBankIncomeGroup = api_data.get("worldBankIncomeGroup")

        # Geometry statistics
        metadata.admUnitCount = int(api_data.get("admUnitCount", 0) or 0)
        metadata.meanVertices = float(api_data.get("meanVertices", 0) or 0)
        metadata.minVertices = int(api_data.get("minVertices", 0) or 0)
        metadata.maxVertices = int(api_data.get("maxVertices", 0) or 0)
        metadata.meanPerimeterLengthKM = float(
            api_data.get("meanPerimeterLengthKM", 0) or 0
        )
        metadata.minPerimeterLengthKM = float(
            api_data.get("minPerimeterLengthKM", 0) or 0
        )
        metadata.maxPerimeterLengthKM = float(
            api_data.get("maxPerimeterLengthKM", 0) or 0
        )
        metadata.meanAreaSqKM = float(api_data.get("meanAreaSqKM", 0) or 0)
        metadata.minAreaSqKM = float(api_data.get("minAreaSqKM", 0) or 0)
        metadata.maxAreaSqKM = float(api_data.get("maxAreaSqKM", 0) or 0)

        # Download URLs
        metadata.staticDownloadLink = api_data.get("staticDownloadLink")
        metadata.gjDownloadURL = api_data.get("gjDownloadURL")
        metadata.tjDownloadURL = api_data.get("tjDownloadURL")
        metadata.simplifiedGeometryGeoJSON = api_data.get("simplifiedGeometryGeoJSON")
        metadata.imagePreview = api_data.get("imagePreview")

    def _create_or_update_adm0_metadata(
        self, api_data: dict[str, Any]
    ) -> AdminBoundary0Metadata:
        """Create or update ADM0 metadata from API response.

        Args:
            api_data: API response data

        Returns:
            AdminBoundary0Metadata object
        """
        iso_code = api_data["boundaryISO"]
        release_type = self.api_client.release_type

        # Query existing
        metadata = AdminBoundary0Metadata.query.filter_by(
            boundaryISO=iso_code, releaseType=release_type
        ).first()

        if metadata:
            logger.debug(f"Updating existing ADM0 metadata for {iso_code}")
            self.stats["adm0_metadata_updated"] += 1
        else:
            logger.debug(f"Creating new ADM0 metadata for {iso_code}")
            metadata = AdminBoundary0Metadata(
                boundaryISO=iso_code, releaseType=release_type
            )
            db.session.add(metadata)
            self.stats["adm0_metadata_created"] += 1

        self._populate_metadata_fields(metadata, api_data)
        return metadata

    def _create_or_update_adm1_metadata(
        self, api_data: dict[str, Any]
    ) -> AdminBoundary1Metadata:
        """Create or update ADM1 metadata from API response.

        Args:
            api_data: API response data

        Returns:
            AdminBoundary1Metadata object
        """
        iso_code = api_data["boundaryISO"]
        release_type = self.api_client.release_type

        # Query existing
        metadata = AdminBoundary1Metadata.query.filter_by(
            boundaryISO=iso_code, releaseType=release_type
        ).first()

        if metadata:
            logger.debug(f"Updating existing ADM1 metadata for {iso_code}")
            self.stats["adm1_metadata_updated"] += 1
        else:
            logger.debug(f"Creating new ADM1 metadata for {iso_code}")
            metadata = AdminBoundary1Metadata(
                boundaryISO=iso_code, releaseType=release_type
            )
            db.session.add(metadata)
            self.stats["adm1_metadata_created"] += 1

        self._populate_metadata_fields(metadata, api_data)
        return metadata

    def _create_or_update_adm1_unit(
        self, shape_id: str, shape_name: str, iso_code: str
    ) -> AdminBoundary1Unit:
        """Create or update ADM1 unit.

        Args:
            shape_id: Unique shape identifier
            shape_name: Name of the state/province
            iso_code: ISO country code

        Returns:
            AdminBoundary1Unit object
        """
        release_type = self.api_client.release_type

        # Query existing
        unit = AdminBoundary1Unit.query.filter_by(
            shapeID=shape_id, releaseType=release_type
        ).first()

        if unit:
            self.stats["adm1_units_updated"] += 1
            unit.shapeName = shape_name
            unit.boundaryISO = iso_code
        else:
            self.stats["adm1_units_created"] += 1
            unit = AdminBoundary1Unit(
                shapeID=shape_id,
                releaseType=release_type,
                boundaryISO=iso_code,
                shapeName=shape_name,
            )
            db.session.add(unit)

        return unit

    def fetch_country_adm0(self, iso_code: str) -> bool:
        """Fetch and import ADM0 boundary metadata for a specific country.

        Args:
            iso_code: ISO 3-letter country code

        Returns:
            True if data was found and imported, False if no data available
        """
        logger.info(f"Fetching ADM0 metadata for {iso_code}")

        api_data = self.api_client.get_boundary_metadata(iso_code, "ADM0")
        if not api_data:
            logger.warning(
                f"No ADM0 data available for {iso_code} in {self.api_client.release_type}"
            )
            return False

        self._create_or_update_adm0_metadata(api_data)
        db.session.commit()
        logger.info(f"Successfully saved ADM0 metadata for {iso_code}")
        return True

    def fetch_country_adm1(self, iso_code: str) -> bool:
        """Fetch and import ADM1 metadata and units for a specific country.

        Downloads simplified GeoJSON to extract unit names (shapeID, shapeName).

        Args:
            iso_code: ISO 3-letter country code

        Returns:
            True if data was found and imported, False if no data available
        """
        logger.info(f"Fetching ADM1 metadata and units for {iso_code}")

        # Fetch ADM1 metadata
        api_data = self.api_client.get_boundary_metadata(iso_code, "ADM1")
        if not api_data:
            logger.warning(
                f"No ADM1 data available for {iso_code} in {self.api_client.release_type}"
            )
            return False

        # Create/update ADM1 metadata
        self._create_or_update_adm1_metadata(api_data)
        db.session.commit()
        logger.info(f"Saved ADM1 metadata for {iso_code}")

        # Download simplified GeoJSON to extract unit names
        simplified_geojson_url = api_data.get("simplifiedGeometryGeoJSON")
        if not simplified_geojson_url:
            logger.warning(f"No simplified GeoJSON URL for {iso_code} ADM1")
            return True  # Metadata was saved, but no units available

        logger.info(f"Downloading simplified GeoJSON for {iso_code}...")
        response = self.api_client.session.get(simplified_geojson_url, timeout=120)
        response.raise_for_status()
        geojson_data = response.json()

        if "features" not in geojson_data:
            raise Exception(f"No features in GeoJSON for {iso_code} ADM1")

        # Extract and save units
        features = geojson_data["features"]
        logger.info(f"Processing {len(features)} ADM1 units for {iso_code}")

        batch_size = 50
        for i in range(0, len(features), batch_size):
            batch = features[i : i + batch_size]

            for feature in batch:
                properties = feature.get("properties", {})
                shape_id = properties.get("shapeID")
                shape_name = properties.get("shapeName")

                if not shape_id:
                    raise Exception(f"Feature missing shapeID in {iso_code} ADM1")

                self._create_or_update_adm1_unit(shape_id, shape_name, iso_code)

            db.session.commit()

            if len(features) > batch_size:
                logger.debug(
                    f"  Committed batch {i // batch_size + 1} "
                    f"({min(i + batch_size, len(features))}/{len(features)})"
                )

        logger.info(f"Successfully saved {len(features)} ADM1 units for {iso_code}")
        return True

    def fetch_all_adm0(self) -> None:
        """Fetch ADM0 metadata for all countries.

        Uses the GeoBoundaries API '/ALL/ADM0/' endpoint to fetch all countries
        in a single API call, then processes and stores the metadata.
        """
        logger.info("Fetching all ADM0 metadata from GeoBoundaries API")

        all_countries = self.api_client.get_all_countries()
        if not all_countries:
            raise Exception("Could not retrieve country list from API")

        logger.info(f"Found {len(all_countries)} countries to process")

        for i, country_data in enumerate(all_countries, 1):
            iso_code = country_data.get("boundaryISO")
            if not iso_code:
                raise Exception(f"Country missing ISO code: {country_data}")

            logger.info(f"[{i}/{len(all_countries)}] Processing {iso_code}")

            self._create_or_update_adm0_metadata(country_data)

            # Commit every 10 countries
            if i % 10 == 0:
                db.session.commit()
                logger.info(f"Progress: {i}/{len(all_countries)} countries processed")

            time.sleep(0.5)  # Rate limiting

        db.session.commit()
        logger.info(
            f"ADM0 fetch completed. "
            f"Created: {self.stats['adm0_metadata_created']}, "
            f"Updated: {self.stats['adm0_metadata_updated']}"
        )

    def fetch_all_adm1(self) -> None:
        """Fetch ADM1 metadata and units for all countries.

        Uses the GeoBoundaries API '/ALL/ADM0/' endpoint to get the country list,
        then fetches ADM1 data for each country individually.
        """
        logger.info("Fetching all ADM1 metadata and units from GeoBoundaries API")

        all_countries = self.api_client.get_all_countries()
        if not all_countries:
            raise Exception("Could not retrieve country list from API")

        logger.info(f"Found {len(all_countries)} countries to process for ADM1")

        for i, country_data in enumerate(all_countries, 1):
            iso_code = country_data.get("boundaryISO")
            if not iso_code:
                raise Exception(f"Country missing ISO code: {country_data}")

            logger.info(
                f"[{i}/{len(all_countries)}] Processing ADM1 for {iso_code} "
                f"({country_data.get('boundaryName') or iso_code})"
            )

            try:
                self.fetch_country_adm1(iso_code)
            except Exception as e:
                # Check if it's a 404 (no ADM1 data available)
                if "404" in str(e) or "No ADM1" in str(e):
                    logger.info(f"No ADM1 data available for {iso_code}")
                else:
                    raise

            if i % 10 == 0:
                logger.info(f"Progress: {i}/{len(all_countries)} countries processed")

            time.sleep(1)  # Rate limiting

        logger.info(
            f"ADM1 fetch completed. "
            f"Metadata created: {self.stats['adm1_metadata_created']}, "
            f"Metadata updated: {self.stats['adm1_metadata_updated']}, "
            f"Units created: {self.stats['adm1_units_created']}, "
            f"Units updated: {self.stats['adm1_units_updated']}"
        )

    def fetch_country(self, iso_code: str) -> None:
        """Fetch both ADM0 and ADM1 boundaries for a specific country.

        Gracefully handles cases where data is not available for the current
        release type (e.g., gbHumanitarian might not have data for all countries).

        Args:
            iso_code: ISO 3-letter country code
        """
        logger.info(f"Fetching all boundaries for {iso_code}")
        
        adm0_found = self.fetch_country_adm0(iso_code)
        adm1_found = self.fetch_country_adm1(iso_code)
        
        if not adm0_found and not adm1_found:
            logger.warning(
                f"No boundary data available for {iso_code} in "
                f"{self.api_client.release_type}"
            )

    def fetch_all(self) -> None:
        """Fetch all boundaries (ADM0 and ADM1) for all countries."""
        logger.info("Fetching all boundaries (ADM0 + ADM1) for all countries")
        self.fetch_all_adm0()
        self.fetch_all_adm1()

    def clear_all_boundaries(self) -> None:
        """Clear all existing boundary data from the database."""
        logger.warning("Clearing all boundary data...")

        # Delete in correct order due to foreign keys
        adm1_units_count = AdminBoundary1Unit.query.count()
        AdminBoundary1Unit.query.delete()

        adm1_metadata_count = AdminBoundary1Metadata.query.count()
        AdminBoundary1Metadata.query.delete()

        adm0_metadata_count = AdminBoundary0Metadata.query.count()
        AdminBoundary0Metadata.query.delete()

        db.session.commit()
        logger.info(
            f"Cleared {adm0_metadata_count} ADM0 metadata, "
            f"{adm1_metadata_count} ADM1 metadata, "
            f"{adm1_units_count} ADM1 units"
        )

    def show_stats(self) -> None:
        """Display statistics about current boundary data."""
        from sqlalchemy import func

        adm0_count = AdminBoundary0Metadata.query.count()
        adm1_metadata_count = AdminBoundary1Metadata.query.count()
        adm1_units_count = AdminBoundary1Unit.query.count()

        logger.info("=== Boundary Data Statistics ===")
        logger.info(f"ADM0 Metadata (Countries): {adm0_count} records")
        logger.info(f"ADM1 Metadata (Country-level): {adm1_metadata_count} records")
        logger.info(f"ADM1 Units (States/Provinces): {adm1_units_count} records")

        # Show counts by release type
        if adm0_count > 0:
            logger.info("\nADM0 Metadata by release type:")
            release_counts = (
                db.session.query(
                    AdminBoundary0Metadata.releaseType,
                    func.count(AdminBoundary0Metadata.boundaryISO).label("count"),
                )
                .group_by(AdminBoundary0Metadata.releaseType)
                .all()
            )
            for release_type, count in release_counts:
                logger.info(f"  - {release_type}: {count} countries")

        if adm1_metadata_count > 0:
            logger.info("\nADM1 Metadata by release type:")
            release_counts = (
                db.session.query(
                    AdminBoundary1Metadata.releaseType,
                    func.count(AdminBoundary1Metadata.boundaryISO).label("count"),
                )
                .group_by(AdminBoundary1Metadata.releaseType)
                .all()
            )
            for release_type, count in release_counts:
                logger.info(f"  - {release_type}: {count} countries")

        if adm1_units_count > 0:
            logger.info("\nADM1 Units by release type:")
            release_counts = (
                db.session.query(
                    AdminBoundary1Unit.releaseType,
                    func.count(AdminBoundary1Unit.shapeID).label("count"),
                )
                .group_by(AdminBoundary1Unit.releaseType)
                .all()
            )
            for release_type, count in release_counts:
                logger.info(f"  - {release_type}: {count} admin1 units")

        if adm0_count > 0:
            logger.info("\nSample countries:")
            sample_countries = AdminBoundary0Metadata.query.limit(5).all()
            for country in sample_countries:
                logger.info(
                    f"  - {country.boundaryISO} ({country.releaseType}): "
                    f"{country.boundaryName or country.boundaryISO}"
                )

        if adm1_units_count > 0:
            logger.info("\nTop 10 countries by ADM1 unit count:")
            country_counts = (
                db.session.query(
                    AdminBoundary1Unit.boundaryISO,
                    AdminBoundary1Unit.releaseType,
                    func.count(AdminBoundary1Unit.shapeID).label("count"),
                )
                .group_by(
                    AdminBoundary1Unit.boundaryISO, AdminBoundary1Unit.releaseType
                )
                .order_by(func.count(AdminBoundary1Unit.shapeID).desc())
                .limit(10)
                .all()
            )

            for country_iso, release_type, count in country_counts:
                country = AdminBoundary0Metadata.query.filter_by(
                    boundaryISO=country_iso, releaseType=release_type
                ).first()
                country_name = (
                    country.boundaryName if country else country_iso or "Unknown"
                )
                logger.info(
                    f"  - {country_iso} ({country_name}) [{release_type}]: "
                    f"{count} admin1 units"
                )


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Fetch geoBoundaries data from API and import into database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Fetch all command
    subparsers.add_parser(
        "fetch-all",
        help="Fetch all boundaries (all countries, all release types, ADM0 + ADM1)",
    )

    # Fetch specific country
    fetch_country_parser = subparsers.add_parser(
        "fetch-country",
        help="Fetch boundaries for a specific country (all release types, ADM0 + ADM1)",
    )
    fetch_country_parser.add_argument(
        "iso_code", help="ISO 3-letter country code (e.g., USA, GBR, DEU)"
    )

    # Stats command
    subparsers.add_parser("stats", help="Show boundary data statistics")

    # Clear all command
    subparsers.add_parser(
        "clear-all", help="Clear all boundary data (use with caution)"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Initialize Flask app context
    with app.app_context():
        try:
            if args.command == "fetch-all":
                logger.info(
                    "Fetching all boundaries (ADM0 + ADM1) for all release types: "
                    f"{', '.join(RELEASE_TYPES)}"
                )
                success_count = 0
                for release_type in RELEASE_TYPES:
                    logger.info(f"=== Processing release type: {release_type} ===")
                    try:
                        fetcher = BoundaryFetcher(release_type=release_type)
                        fetcher.fetch_all()
                        logger.info(f"=== Completed {release_type} ===")
                        success_count += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to fetch all boundaries for {release_type}: {str(e)}"
                        )
                        logger.info(f"=== Skipping {release_type} ===")
                        continue
                
                if success_count == 0:
                    logger.error("Failed to fetch boundaries for all release types")
                    return 1
                elif success_count < len(RELEASE_TYPES):
                    logger.warning(
                        f"Completed {success_count}/{len(RELEASE_TYPES)} release types"
                    )

            elif args.command == "fetch-country":
                iso_code = args.iso_code.upper()
                logger.info(
                    f"Fetching {iso_code} boundaries (ADM0 + ADM1) for all "
                    f"release types: {', '.join(RELEASE_TYPES)}"
                )

                success_count = 0
                for release_type in RELEASE_TYPES:
                    logger.info(f"=== Processing release type: {release_type} ===")
                    try:
                        fetcher = BoundaryFetcher(release_type=release_type)
                        fetcher.fetch_country(iso_code)
                        logger.info(f"=== Completed {release_type} ===")
                        success_count += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to fetch {iso_code} for {release_type}: {str(e)}"
                        )
                        logger.info(f"=== Skipping {release_type} ===")
                        continue
                
                if success_count == 0:
                    logger.error(
                        f"Failed to fetch {iso_code} for all release types"
                    )
                    return 1
                elif success_count < len(RELEASE_TYPES):
                    logger.warning(
                        f"Completed {success_count}/{len(RELEASE_TYPES)} release types"
                    )

            elif args.command == "stats":
                fetcher = BoundaryFetcher()
                fetcher.show_stats()

            elif args.command == "clear-all":
                response = input(
                    "Are you sure you want to clear all boundary data? "
                    "Type 'yes' to confirm: "
                )
                if response.lower() == "yes":
                    fetcher = BoundaryFetcher()
                    fetcher.clear_all_boundaries()
                else:
                    logger.info("Operation cancelled")

        except KeyboardInterrupt:
            logger.warning("Operation interrupted by user")
            return 1
        except Exception as e:
            logger.error(f"Error: {str(e)}", exc_info=True)
            return 1

        # Show final stats if fetch was performed
        if args.command.startswith("fetch"):
            fetcher = BoundaryFetcher()
            fetcher.show_stats()

    return 0


if __name__ == "__main__":
    sys.exit(main())
