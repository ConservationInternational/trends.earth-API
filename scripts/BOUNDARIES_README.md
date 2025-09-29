# GeoBoundaries Data Import

This directory contains scripts for importing administrative boundary data from geoBoundaries into the Trends.Earth API database.

## Prerequisites

1. **Docker Environment**: Admin container with GDAL and database access
2. **Admin privileges**: Database write operations require ADMIN or SUPERADMIN role
3. **Data files**: GeoBoundaries geopackage files should be mounted or copied into the container:
   - `geoBoundariesCGAZ_ADM0.gpkg` (Country boundaries)
   - `geoBoundariesCGAZ_ADM1.gpkg` (State/Province boundaries)

## Import Script Features

The `import_boundaries.py` script provides a **BoundaryImporter Class** with comprehensive functionality:
- Import ADM0 and ADM1 data from geopackage files
- Batch processing for memory efficiency (50 records for ADM0, 100 for ADM1)
- Data validation and error handling
- Statistics reporting and progress logging
- CLI interface with multiple commands:
  - `import-adm0` - Import country boundaries
  - `import-adm1` - Import state/province boundaries  
  - `import-all` - Import from directory containing both files
  - `clear-all` - Remove all boundary data (use with caution)
  - `stats` - Display current data statistics
  - `validate` - Check data integrity

## Usage Instructions

### Start Admin Container and Copy Data Files
```bash
# Start the admin container for database operations
docker compose -f docker-compose.admin.yml up -d

# Copy data files into the admin container
docker cp /path/to/geoBoundariesCGAZ_ADM0.gpkg trendsearth-api-admin-1:/opt/gef-api/
docker cp /path/to/geoBoundariesCGAZ_ADM1.gpkg trendsearth-api-admin-1:/opt/gef-api/

# Access the container shell
docker exec -it trendsearth-api-admin-1 bash
```

### Import Commands (Inside Admin Container)
```bash
# Navigate to scripts directory
cd /opt/gef-api/scripts

# Import Country Boundaries (ADM0)
python import_boundaries.py import-adm0 ../geoBoundariesCGAZ_ADM0.gpkg

# Import State/Province Boundaries (ADM1)
python import_boundaries.py import-adm1 ../geoBoundariesCGAZ_ADM1.gpkg

# Import All Boundaries from Directory (if files are in /opt/gef-api/)
python import_boundaries.py import-all ../

# View Statistics
python import_boundaries.py stats

# Validate Data Integrity
python import_boundaries.py validate

# Clear All Data (Use with Caution)
python import_boundaries.py clear-all
```

### Cleanup Admin Container
```bash
# Stop and remove admin container when done
docker compose -f docker-compose.admin.yml down
```

## Data Structure

The import script processes geoBoundaries data and stores it in two database tables:

### AdminBoundary0 (Countries)
- `id`: ISO 3-letter country code (e.g., 'USA', 'DEU')
- `shape_group`, `shape_type`, `shape_name`: Metadata from geoBoundaries
- `geom_wkt`: Geometry stored as Well-Known Text
- `created_at`, `updated_at`: Timestamps

### AdminBoundary1 (States/Provinces)
- `shape_id`: Unique identifier (e.g., 'USA-ADM1-12345')
- `id`: Country ISO code (foreign key to AdminBoundary0)
- `shape_name`: Administrative unit name
- `shape_group`, `shape_type`: Metadata from geoBoundaries  
- `geom_wkt`: Geometry stored as Well-Known Text
- `created_at`, `updated_at`: Timestamps

## Expected Data Volumes

- **ADM0**: ~218 country records
- **ADM1**: ~3,224 administrative unit records

## Import Process

1. The script reads geopackage files using GDAL/OGR
2. Extracts geometry and attribute data for each feature
3. Converts geometries to WKT format for database storage
4. Creates or updates database records with batch commits
5. Maintains foreign key relationships between ADM0 and ADM1
6. Provides progress logging and error handling

## Data File Management

### Data File Options

**Option 1: Copy files into container** (recommended):
```bash
# Copy data files directly into running admin container
docker cp /path/to/geoBoundariesCGAZ_ADM0.gpkg trendsearth-api-admin-1:/opt/gef-api/
docker cp /path/to/geoBoundariesCGAZ_ADM1.gpkg trendsearth-api-admin-1:/opt/gef-api/
```

**Option 2: Mount data files as volumes** in `docker-compose.admin.yml`:
```yaml
services:
  admin:
    volumes:
      - ./gefapi:/opt/gef-api/gefapi
      - ./migrations:/opt/gef-api/migrations
      - ./scripts:/opt/gef-api/scripts          # Scripts are mounted
      - ./data:/opt/gef-api/data:ro             # Mount data directory as read-only
      - /external/data:/data:ro                 # Mount external data source
```



## Container Environment Details

### Dependencies
- **GDAL**: Pre-installed in admin container
- **Python dependencies**: Automatically available via project requirements
- **Database connection**: Configured via environment variables

### Environment Variables
The admin container uses production database configuration.