#!/bin/bash
# Initialize PostGIS extension for Trends.Earth API
# This script runs automatically when PostgreSQL container starts

set -e

echo "Creating PostGIS extensions in staging database..."

# Add PostGIS extensions to the main database (staging database)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS postgis_topology;
EOSQL

echo "Creating test database and PostGIS extensions..."

# Create test database
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE gef_test;
EOSQL

# Add PostGIS extensions to test database
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "gef_test" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS postgis_topology;
EOSQL

# Grant permissions on test database
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    GRANT ALL PRIVILEGES ON DATABASE gef_test TO "$POSTGRES_USER";
EOSQL

echo "PostGIS initialization complete"