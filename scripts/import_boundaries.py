#!/usr/bin/env python3
"""
GeoBoundaries Data Import Tool

This script provides a command-line interface for importing administrative boundary
data from geoBoundaries geopackage files into the database.

SECURITY NOTICE: This tool requires ADMIN or SUPERADMIN privileges for database
write operations.

Usage examples:

# Import ADM0 (country) boundaries
python import_boundaries.py import-adm0 data/geoBoundariesCGAZ_ADM0.gpkg

# Import ADM1 (state/province) boundaries
python import_boundaries.py import-adm1 data/geoBoundariesCGAZ_ADM1.gpkg

# Import both levels from directory containing .gpkg files
python import_boundaries.py import-all data/

# Clear existing boundary data (use with caution)
python import_boundaries.py clear-all

# Show import statistics
python import_boundaries.py stats

# Validate existing boundary data
python import_boundaries.py validate
"""

import argparse
import logging
import os
from pathlib import Path
import sys

# Add the parent directory to the Python path to import gefapi
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    from osgeo import ogr
except ImportError:
    print("ERROR: GDAL Python bindings are required. Install with: pip install GDAL")
    sys.exit(1)

from gefapi import app, db
from gefapi.models.boundary import AdminBoundary0, AdminBoundary1

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class BoundaryImporter:
    """Handles import of geoBoundaries data into the database."""

    def __init__(self):
        """Initialize the importer."""
        self.stats = {
            "adm0_imported": 0,
            "adm0_updated": 0,
            "adm0_errors": 0,
            "adm1_imported": 0,
            "adm1_updated": 0,
            "adm1_errors": 0,
        }

    def import_adm0_from_geopackage(self, gpkg_path):
        """
        Import ADM0 (country level) boundaries from geopackage.

        Args:
            gpkg_path (str): Path to the geoBoundaries ADM0 geopackage file
        """
        if not os.path.exists(gpkg_path):
            raise FileNotFoundError(f"Geopackage file not found: {gpkg_path}")

        logger.info(f"Opening geopackage: {gpkg_path}")

        # Open the geopackage
        driver = ogr.GetDriverByName("GPKG")
        datasource = driver.Open(gpkg_path, 0)  # 0 = read-only

        if datasource is None:
            raise RuntimeError(f"Could not open geopackage: {gpkg_path}")

        # Get the layer (should be 'globalADM0')
        layer = datasource.GetLayer(0)
        if layer is None:
            raise RuntimeError("Could not get layer from geopackage")

        layer_name = layer.GetName()
        feature_count = layer.GetFeatureCount()
        logger.info(f"Processing layer '{layer_name}' with {feature_count} features")

        # Process each feature
        for feature in layer:
            try:
                # Extract field values
                country_id = feature.GetField("id")
                shape_group = feature.GetField("shapeGroup")
                shape_type = feature.GetField("shapeType")
                shape_name = feature.GetField("shapeName")

                # Get geometry as WKT for conversion to PostGIS geometry
                geometry = feature.GetGeometryRef()
                if geometry:
                    geom_wkt = geometry.ExportToWkt()
                else:
                    logger.warning(f"No geometry for country {country_id}")
                    geom_wkt = None

                # Check if record already exists
                existing = AdminBoundary0.query.filter_by(id=country_id).first()

                if existing:
                    # Update existing record
                    existing.shape_group = shape_group
                    existing.shape_type = shape_type
                    existing.shape_name = shape_name
                    if geom_wkt:
                        from geoalchemy2 import WKTElement

                        existing.geometry = WKTElement(geom_wkt, srid=4326)
                    self.stats["adm0_updated"] += 1
                    logger.debug(f"Updated country: {country_id} - {shape_name}")
                else:
                    # Create new record with PostGIS geometry
                    from geoalchemy2 import WKTElement

                    geometry_elem = (
                        WKTElement(geom_wkt, srid=4326) if geom_wkt else None
                    )
                    boundary = AdminBoundary0(
                        id=country_id,
                        shape_group=shape_group,
                        shape_type=shape_type,
                        shape_name=shape_name,
                        geometry=geometry_elem,
                    )
                    db.session.add(boundary)
                    self.stats["adm0_imported"] += 1
                    logger.debug(f"Imported country: {country_id} - {shape_name}")

                # Commit every 50 records to avoid memory issues
                if (self.stats["adm0_imported"] + self.stats["adm0_updated"]) % 50 == 0:
                    db.session.commit()

            except Exception as e:
                logger.error(f"Error processing feature {feature.GetFID()}: {str(e)}")
                self.stats["adm0_errors"] += 1
                continue

        # Final commit
        db.session.commit()
        logger.info(
            f"ADM0 import completed. Imported: {self.stats['adm0_imported']}, "
            f"Updated: {self.stats['adm0_updated']}, "
            f"Errors: {self.stats['adm0_errors']}"
        )

    def import_adm1_from_geopackage(self, gpkg_path):
        """
        Import ADM1 (state/province level) boundaries from geopackage.

        Args:
            gpkg_path (str): Path to the geoBoundaries ADM1 geopackage file
        """
        if not os.path.exists(gpkg_path):
            raise FileNotFoundError(f"Geopackage file not found: {gpkg_path}")

        logger.info(f"Opening geopackage: {gpkg_path}")

        # Open the geopackage
        driver = ogr.GetDriverByName("GPKG")
        datasource = driver.Open(gpkg_path, 0)  # 0 = read-only

        if datasource is None:
            raise RuntimeError(f"Could not open geopackage: {gpkg_path}")

        # Get the layer (should be 'globalADM1')
        layer = datasource.GetLayer(0)
        if layer is None:
            raise RuntimeError("Could not get layer from geopackage")

        layer_name = layer.GetName()
        feature_count = layer.GetFeatureCount()
        logger.info(f"Processing layer '{layer_name}' with {feature_count} features")

        # Process each feature
        for feature in layer:
            try:
                # Extract field values
                shape_id = feature.GetField("shapeID")
                country_id = feature.GetField("id")
                shape_name = feature.GetField("shapeName")
                shape_group = feature.GetField("shapeGroup")
                shape_type = feature.GetField("shapeType")

                # Get geometry as WKT for conversion to PostGIS geometry
                geometry = feature.GetGeometryRef()
                if geometry:
                    geom_wkt = geometry.ExportToWkt()
                else:
                    logger.warning(f"No geometry for {shape_id}")
                    geom_wkt = None

                # Check if record already exists
                existing = AdminBoundary1.query.filter_by(shape_id=shape_id).first()

                if existing:
                    # Update existing record
                    existing.id = country_id
                    existing.shape_name = shape_name
                    existing.shape_group = shape_group
                    existing.shape_type = shape_type
                    if geom_wkt:
                        from geoalchemy2 import WKTElement

                        existing.geometry = WKTElement(geom_wkt, srid=4326)
                    self.stats["adm1_updated"] += 1
                    logger.debug(f"Updated admin1: {shape_id} - {shape_name}")
                else:
                    # Create new record with PostGIS geometry
                    from geoalchemy2 import WKTElement

                    geometry_elem = (
                        WKTElement(geom_wkt, srid=4326) if geom_wkt else None
                    )
                    boundary = AdminBoundary1(
                        shape_id=shape_id,
                        id=country_id,
                        shape_name=shape_name,
                        shape_group=shape_group,
                        shape_type=shape_type,
                        geometry=geometry_elem,
                    )
                    db.session.add(boundary)
                    self.stats["adm1_imported"] += 1
                    logger.debug(f"Imported admin1: {shape_id} - {shape_name}")

                # Commit every 100 records for ADM1 (more features)
                if (
                    self.stats["adm1_imported"] + self.stats["adm1_updated"]
                ) % 100 == 0:
                    db.session.commit()
                    logger.info(
                        f"Progress: "
                        f"{self.stats['adm1_imported'] + self.stats['adm1_updated']} "
                        f"features processed..."
                    )

            except Exception as e:
                logger.error(f"Error processing feature {feature.GetFID()}: {str(e)}")
                self.stats["adm1_errors"] += 1
                continue

        # Final commit
        db.session.commit()
        logger.info(
            f"ADM1 import completed. Imported: {self.stats['adm1_imported']}, "
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
                logger.info(f"  - {country.id}: {country.shape_name}")

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
                country_name = country.shape_name if country else "Unknown"
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
        adm0_no_geom = AdminBoundary0.query.filter_by(geom_wkt=None).count()
        adm1_no_geom = AdminBoundary1.query.filter_by(geom_wkt=None).count()

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
        description="Import geoBoundaries administrative boundary data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Import ADM0 command
    import_adm0_parser = subparsers.add_parser(
        "import-adm0", help="Import ADM0 (country) boundaries"
    )
    import_adm0_parser.add_argument(
        "gpkg_path", help="Path to geoBoundariesCGAZ_ADM0.gpkg file"
    )

    # Import ADM1 command
    import_adm1_parser = subparsers.add_parser(
        "import-adm1", help="Import ADM1 (state/province) boundaries"
    )
    import_adm1_parser.add_argument(
        "gpkg_path", help="Path to geoBoundariesCGAZ_ADM1.gpkg file"
    )

    # Import all command
    import_all_parser = subparsers.add_parser(
        "import-all", help="Import both ADM0 and ADM1 from directory"
    )
    import_all_parser.add_argument("data_dir", help="Directory containing .gpkg files")

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
        return None

    # Initialize Flask app context
    with app.app_context():
        importer = BoundaryImporter()

        try:
            if args.command == "import-adm0":
                importer.import_adm0_from_geopackage(args.gpkg_path)

            elif args.command == "import-adm1":
                importer.import_adm1_from_geopackage(args.gpkg_path)

            elif args.command == "import-all":
                data_dir = Path(args.data_dir)

                # Look for ADM0 file
                adm0_files = list(data_dir.glob("*ADM0*.gpkg"))
                if adm0_files:
                    logger.info(f"Found ADM0 file: {adm0_files[0]}")
                    importer.import_adm0_from_geopackage(str(adm0_files[0]))
                else:
                    logger.warning("No ADM0 .gpkg file found")

                # Look for ADM1 file
                adm1_files = list(data_dir.glob("*ADM1*.gpkg"))
                if adm1_files:
                    logger.info(f"Found ADM1 file: {adm1_files[0]}")
                    importer.import_adm1_from_geopackage(str(adm1_files[0]))
                else:
                    logger.warning("No ADM1 .gpkg file found")

            elif args.command == "clear-all":
                response = input(
                    "Are you sure you want to clear all boundary data? "
                    "Type 'yes' to confirm: "
                )
                if response.lower() == "yes":
                    importer.clear_all_boundaries()
                else:
                    logger.info("Operation cancelled")

            elif args.command == "stats":
                importer.show_stats()

            elif args.command == "validate":
                importer.validate_data()

        except Exception as e:
            logger.error(f"Error: {str(e)}")
            return 1

        # Show final stats if import was performed
        if args.command.startswith("import"):
            importer.show_stats()

    return 0


if __name__ == "__main__":
    sys.exit(main())
