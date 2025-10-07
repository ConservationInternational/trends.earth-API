# Staging Deployment Fix Summary

## Issues Fixed

### 1. PostGIS Extension Missing (Latest Fix)

**Problem**: Deployments were failing with:
```
(psycopg2.errors.UndefinedObject) type "geometry" does not exist
LINE 7:  geometry geometry(MULTIPOLYGON,4326),
```

**Root Cause**: The PostGIS extension was not being installed in the database when the PostgreSQL volume persisted across deployments. The `init-postgis.sh` script only runs when a database is first created, not on subsequent container restarts.

**Solution**: Enhanced `run_db_migrations.py` to:
- Added `ensure_postgis_extensions()` function that runs before migrations
- Automatically checks if PostGIS extension exists in the database
- Creates `postgis` and `postgis_topology` extensions if missing
- Fails fast in staging environment if extension cannot be created
- Made `config/db/init-postgis.sh` executable for better Docker compatibility

**Files Changed**:
- `run_db_migrations.py`: Added 53 lines for PostGIS extension checking and installation
- `config/db/init-postgis.sh`: Made executable and improved messaging

### 2. Migration Multiple Heads Issue

**Problem**: Deployments were failing with:
```
ERROR [alembic.env] Migration execution failed: Multiple head revisions are present for given argument 'head'; please specify a specific target revision, '<branchname>@head' to narrow to a specific head, or 'heads' for all heads
```

**Root Cause**: The database `alembic_version` table had inconsistent state, likely from concurrent migrations or interrupted deployments.

**Solution**: Enhanced `run_db_migrations.py` to:
- Detect multiple heads before attempting upgrade
- Fallback to specific target head (`3eedf39b54dd`) when multiple heads found
- Added multiple retry strategies with better error handling
- Improved logging for debugging

### 3. Docker Swarm Race Conditions

**Problem**: Services failing to update with:
```
failed to update service trends-earth-staging_***: Error response from daemon: rpc error: code = Unknown desc = update out of sequence
```

**Root Cause**: Docker Swarm was attempting concurrent updates of services, causing race conditions.

**Solution**: 
- Changed deployment strategy from rolling updates to fresh stack deployment
- Added stack removal before deployment to ensure clean state
- Added proper update configurations to all services in Docker Compose
- Configured migrate service with `stop-first` ordering to prevent concurrent migrations

## Files Changed

### `.github/workflows/deploy-staging.yml`
- Replaced rolling updates with stack removal and fresh deployment
- Added proper stack cleanup before deployment
- Improved service startup timing

### `docker-compose.staging.yml`
- Added `update_config` sections to all services
- Configured migrate service with `parallelism: 1` and `order: stop-first`
- Added delay configurations to prevent race conditions

### `run_db_migrations.py`
- Added migration head detection and resolution logic
- Enhanced error handling with multiple fallback strategies
- Improved logging and debugging information

## Testing Recommendations

1. **Test Migration Recovery**: Verify the migration script handles multiple heads correctly
2. **Test Deployment Reliability**: Run multiple staging deployments to ensure race conditions are resolved
3. **Monitor Logs**: Check deployment logs for any remaining issues

## Prevention Measures

- Migration service now runs with proper constraints to prevent concurrency
- Stack deployment strategy prevents Docker Swarm update conflicts
- Enhanced error handling provides better debugging information
- Improved logging helps identify issues quickly