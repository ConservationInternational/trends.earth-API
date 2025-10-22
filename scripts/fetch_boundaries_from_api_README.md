# GeoBoundaries API Data Fetcher

This script fetches administrative boundary data directly from the [GeoBoundaries API](https://www.geoboundaries.org/) and imports it into the Trends.Earth database. It retrieves country (ADM0) and state/province (ADM1) boundaries with complete metadata and geometries.

## Overview

The `fetch_boundaries_from_api.py` script provides a command-line interface for:
- Fetching ADM0 (country-level) boundaries
- Fetching ADM1 (state/province-level) boundaries
- Downloading TopoJSON geometries for efficient storage
- Storing complete metadata from GeoBoundaries API responses
- Validating and managing boundary data

## Requirements

### Database Access
This script connects directly to the PostgreSQL database and bypasses the API authentication layer. When running from within the Docker container, it uses the database credentials configured in the environment file (`prod.env` or `develop.env`).

### Python Dependencies
- `requests` - HTTP requests to GeoBoundaries API
- `topojson` - Converting TopoJSON to GeoJSON
- `shapely` - Geometry processing
- `geoalchemy2` - PostGIS geometry handling
- All standard Trends.Earth API dependencies (Flask, SQLAlchemy, etc.)

## Usage

### Running from Admin Docker Container

The recommended way to run this script is from within the admin Docker container using the normal Docker Compose workflow documented in the main [README.md](../README.md).

#### Step 1: Start the Admin Container

**For Production Environment:**
```bash
# Start the admin container (uses prod.env by default)
docker compose -f docker-compose.admin.yml up -d
```

**For Staging Environment:**
```bash
# Modify docker-compose.admin.yml to use staging.env instead of prod.env
# Or use environment variable override:
docker compose -f docker-compose.admin.yml up -d
```

> **Note**: The admin container is configured for production by default (`prod.env`). To use staging, you can either:
> 1. Temporarily edit `docker-compose.admin.yml` to change `prod.env` to `staging.env`
> 2. Create a custom compose file specifically for staging admin tasks
> 3. Use `docker compose run` with environment overrides

This starts a lightweight container with:
- Access to the configured environment (production or staging)
- All API dependencies pre-installed
- Volume mounts for the `scripts/` directory
- Database connection configured via environment file (`prod.env` or `staging.env`)

#### Step 2: Execute the Script

Once the admin container is running, you can execute the script using `docker exec`:

```bash
# Basic syntax
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py [command]
```

**Available Commands:**

```bash
# Fetch all ADM0 (country) boundaries
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-adm0

# Fetch all ADM1 (state/province) boundaries
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-adm1

# Fetch both ADM0 and ADM1 boundaries (complete import)
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-all

# Fetch boundaries for a specific country (ISO 3-letter code)
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-country USA

# Fetch only ADM0 for a specific country
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-country USA --adm-level ADM0

# Fetch only ADM1 for a specific country
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-country DEU --adm-level ADM1

# Show statistics about imported boundaries
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py stats

# Validate data integrity
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py validate

# Clear all boundary data (use with caution!)
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py clear-all
```

#### Step 3: Monitor Progress

The script outputs detailed logging information:

```bash
# Example output when fetching ADM0 boundaries
2025-10-22 12:00:00 - INFO - Fetching all ADM0 boundaries from GeoBoundaries API
2025-10-22 12:00:01 - INFO - Found 195 countries to process
2025-10-22 12:00:02 - INFO - [1/195] Processing USA
2025-10-22 12:00:03 - INFO - [2/195] Processing GBR
...
2025-10-22 12:15:00 - INFO - ADM0 fetch completed. Imported: 195, Updated: 0, Errors: 0
```

#### Step 4: Cleanup

```bash
# Stop and remove the admin container
docker compose -f docker-compose.admin.yml down
```

## Command Details

### `fetch-adm0`
Fetches all ADM0 (country-level) boundaries from the GeoBoundaries API.

- Retrieves ~195 countries worldwide
- Downloads TopoJSON geometries
- Stores metadata (population, area, license info, etc.)
- Takes approximately 10-15 minutes
- Rate-limited to be respectful of the API (0.5s delay between requests)

### `fetch-adm1`
Fetches all ADM1 (state/province-level) boundaries for all countries.

- **Prerequisite**: ADM0 data must be fetched first
- Processes all countries in the database
- Downloads detailed sub-national boundaries
- Takes approximately 30-60 minutes depending on data availability
- Rate-limited with 1s delay between countries
- Some countries may not have ADM1 data available

### `fetch-all`
Convenience command that runs both `fetch-adm0` and `fetch-adm1` sequentially.

- Complete import from start to finish
- Takes approximately 45-75 minutes total
- Automatically handles dependencies (ADM0 before ADM1)

### `fetch-country <ISO>`
Fetches boundaries for a specific country using its ISO 3-letter code.

**Arguments:**
- `ISO` - Three-letter ISO country code (e.g., USA, GBR, DEU, FRA, JPN)
- `--adm-level` - Optional: Specify `ADM0`, `ADM1`, or `both` (default: both)

**Examples:**
```bash
# Fetch both levels for Germany
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-country DEU

# Fetch only country boundary for Japan
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-country JPN --adm-level ADM0

# Fetch only state/province boundaries for USA
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-country USA --adm-level ADM1
```

### `stats`
Displays statistics about the current boundary data in the database.

**Output includes:**
- Total count of ADM0 records
- Total count of ADM1 records
- Sample country names
- Top 10 countries by number of ADM1 subdivisions

### `validate`
Validates the integrity of imported boundary data.

**Checks performed:**
- Orphaned ADM1 records (references to non-existent countries)
- Missing geometries
- Data consistency

### `clear-all`
**DANGEROUS**: Removes all boundary data from the database.

- Requires confirmation (`yes` typed exactly)
- Deletes both ADM0 and ADM1 records
- Cannot be undone
- Use with extreme caution in production environments

## Data Structure

### AdminBoundary0 (Countries)
Stores country-level boundaries with the following key fields:
- `id` - ISO 3-letter country code (primary key)
- `boundary_name` - Country name
- `geometry` - PostGIS geometry (polygon/multipolygon)
- Geographic metadata (continent, UNSDG region, World Bank income group)
- Geometry statistics (area, perimeter, vertex counts)
- Source information (license, update dates, download URLs)

### AdminBoundary1 (States/Provinces)
Stores sub-national administrative boundaries:
- `shape_id` - Unique identifier for each subdivision (primary key)
- `id` - ISO country code (foreign key to AdminBoundary0)
- `geometry` - PostGIS geometry for the subdivision
- Similar metadata structure as ADM0

## Environment Configuration

The script uses the Flask application context and requires:
- Database connection (configured via environment variables)
- PostGIS-enabled PostgreSQL database
- Network access to `https://www.geoboundaries.org/`

### Staging vs Production

The script connects to different databases depending on which environment file is loaded:

**Production** (default for admin container):
- Environment file: `prod.env`
- Database: Production PostgreSQL instance
- Use case: Importing boundaries for live production data

**Staging**:
- Environment file: `staging.env`
- Database: Staging PostgreSQL instance
- Use case: Testing boundary imports before production deployment

To run against staging, modify the `docker-compose.admin.yml` file temporarily:

```yaml
# Change this line:
env_file:
  - prod.env

# To this:
env_file:
  - staging.env
```

### Environment Variables
- `DATABASE_URL` - PostgreSQL connection string with PostGIS
- `ENVIRONMENT` - Environment name (`prod`, `staging`, or `dev`)
- Standard Trends.Earth API configuration

## API Information

**GeoBoundaries API:**
- Base URL: `https://www.geoboundaries.org/api/current/`
- Release Type: `gbOpen` (open access boundaries)
- No authentication required
- Free to use with attribution
- Documentation: https://www.geoboundaries.org/api.html

**Alternative Release Types:**
- `gbOpen` - Freely available boundaries (default)
- `gbHumanitarian` - Humanitarian response boundaries
- `gbAuthoritative` - Authoritative government sources

## Error Handling

The script includes comprehensive error handling:
- HTTP retry logic (3 attempts with exponential backoff)
- Network timeout handling (30 seconds per request)
- API rate limiting compliance
- Database transaction rollback on errors
- Detailed error logging

**Common Issues:**

1. **Network timeouts**: The script will retry automatically
2. **404 errors**: Some countries may not have ADM1 data available (expected)
3. **Database errors**: Check database connection and PostGIS installation
4. **Memory issues**: Large countries may require significant RAM for geometry processing

## Performance Considerations

- **ADM0 import**: ~195 countries, 10-15 minutes, ~50MB database storage
- **ADM1 import**: ~10,000+ subdivisions, 30-60 minutes, ~500MB database storage
- **Rate limiting**: Built-in delays prevent API abuse
- **Batch commits**: Database commits every 10 records for ADM0
- **Memory usage**: Can be high during geometry processing for large countries

## Best Practices

1. **Initial Setup**: Run `fetch-all` to populate complete database
2. **Updates**: Re-run commands to update existing data (script handles updates)
3. **Validation**: Always run `validate` after import to check data integrity
4. **Monitoring**: Watch logs for errors during long-running imports
5. **Backup**: Back up database before running `clear-all`
6. **Production**: Use admin container to avoid disrupting running services

## Troubleshooting

### Script fails to start
**Check:**
- Admin container is running: `docker ps | grep admin`
- Script path is correct: `/opt/gef-api/scripts/fetch_boundaries_from_api.py`
- Container has network access

### Database connection errors
**Check:**
- `prod.env` file exists and contains valid `DATABASE_URL`
- PostgreSQL service is running
- Database has PostGIS extension: `CREATE EXTENSION postgis;`

### API request failures
**Check:**
- Network connectivity to `geoboundaries.org`
- Firewall rules allow outbound HTTPS
- API is not experiencing downtime (check status)

### Memory issues
**Solutions:**
- Process countries individually using `fetch-country`
- Increase Docker memory limits
- Process ADM0 and ADM1 separately

## Related Files

- **Main README**: [../README.md](../README.md) - Complete API documentation
- **Docker Compose**: [../docker-compose.admin.yml](../docker-compose.admin.yml) - Admin container configuration
- **Models**: `gefapi/models/boundary.py` - Database model definitions
- **Migration**: `migrations/versions/*_add_boundary_tables.py` - Database schema

## Examples

### Complete Fresh Import
```bash
# Start admin container
docker compose -f docker-compose.admin.yml up -d

# Fetch all boundaries (45-75 minutes)
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-all

# Validate data
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py validate

# Show statistics
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py stats

# Cleanup
docker compose -f docker-compose.admin.yml down
```

### Update Specific Country
```bash
# Start admin container
docker compose -f docker-compose.admin.yml up -d

# Update France boundaries
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-country FRA

# Cleanup
docker compose -f docker-compose.admin.yml down
```

### Quick Stats Check
```bash
# Start admin container
docker compose -f docker-compose.admin.yml up -d

# View current statistics
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py stats

# Cleanup
docker compose -f docker-compose.admin.yml down
```

### Testing on Staging Before Production
```bash
# Step 1: Modify docker-compose.admin.yml to use staging.env
sed -i 's/prod.env/staging.env/g' docker-compose.admin.yml

# Step 2: Start admin container (now connected to staging)
docker compose -f docker-compose.admin.yml up --build -d

# Step 3: Test import on staging
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-country USA

# Step 4: Validate staging data
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py validate

# Step 5: Cleanup
docker compose -f docker-compose.admin.yml down

# Step 6: Revert to production configuration
sed -i 's/staging.env/prod.env/g' docker-compose.admin.yml

# Step 7: Run on production
docker compose -f docker-compose.admin.yml up -d
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-country USA
docker compose -f docker-compose.admin.yml down
```

## Security Considerations

⚠️ **IMPORTANT SECURITY NOTES:**

1. **Direct Database Access**: This script modifies the production database directly, bypassing API authentication
2. **Container Credentials**: Uses database credentials from environment files (not user-level permissions)
3. **Use Admin Container**: Isolates operations from running services
4. **Environment Files**: Never commit `prod.env` to version control
5. **Clear-All Command**: Extremely dangerous - requires explicit confirmation
6. **Network Access**: Script makes external API calls to geoboundaries.org

## License and Attribution

Boundary data sourced from **GeoBoundaries** (www.geoboundaries.org):
- Data license: Varies by country (stored in `boundary_license` field)
- API: Open access, no authentication required
- Attribution: Required when using boundary data (see license details)

**Citation:**
```
Runfola D, et al. (2020) geoBoundaries: A global database of political 
administrative boundaries. PLoS ONE 15(4): e0231866.
```

## Support

For issues with:
- **This script**: Check logs, validate environment, review troubleshooting section
- **GeoBoundaries API**: Visit https://www.geoboundaries.org/
- **Trends.Earth API**: See main [README.md](../README.md) for support information
