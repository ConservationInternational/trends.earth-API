# GeoBoundaries Boundary Fetcher

Fetches administrative boundary data from the [GeoBoundaries API](https://www.geoboundaries.org/) and imports it into the Trends.Earth database. Automatically fetches all release types (gbOpen, gbHumanitarian, gbAuthoritative) for both country (ADM0) and state/province (ADM1) levels.

## Quick Start

### 1. Start the Admin Container

```bash
docker compose -f docker-compose.admin.yml up --build -d
```

### 2. Run Commands

```bash
# Fetch all boundaries (all countries, all release types, ADM0 + ADM1)
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-all

# Fetch specific country (all release types, ADM0 + ADM1)
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-country USA

# Show statistics
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py stats

# Clear all boundary data (requires confirmation)
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py clear-all
```

### 3. Stop the Container

```bash
docker compose -f docker-compose.admin.yml down
```

## Commands

- **`fetch-all`** - Fetch all boundaries for all countries (45-75 minutes)
- **`fetch-country <ISO>`** - Fetch boundaries for one country (e.g., USA, GBR, DEU)
- **`stats`** - Show current boundary data statistics
- **`clear-all`** - Delete all boundary data (requires `yes` confirmation)

## Environment

The admin container uses `prod.env` by default. To use staging, edit `docker-compose.admin.yml` and change `prod.env` to `staging.env`.

## What Gets Imported

For each country, the script automatically attempts to fetch:
- **3 release types**: gbOpen, gbHumanitarian, gbAuthoritative
- **2 admin levels**: ADM0 (country) and ADM1 (states/provinces)
- **Metadata only**: Download URLs are stored, geometries are NOT stored in the database

**Important**: Not all countries have data for all release types. For example, the USA has `gbOpen` data but not `gbHumanitarian` or `gbAuthoritative`. The script gracefully skips release types that don't have data available and continues with the next one.

Clients use the stored `gjDownloadURL` or `tjDownloadURL` fields to fetch geometries when needed.

## Examples

### Complete Import
```bash
docker compose -f docker-compose.admin.yml up -d
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-all
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py stats
docker compose -f docker-compose.admin.yml down
```

### Update Single Country
```bash
docker compose -f docker-compose.admin.yml up -d
docker exec -it trendsearth-api-admin-1 python /opt/gef-api/scripts/fetch_boundaries_from_api.py fetch-country FRA
docker compose -f docker-compose.admin.yml down
```