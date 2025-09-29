-- Initialize PostGIS extension for Trends.Earth API
-- This script runs automatically when PostgreSQL container starts

\echo 'Creating PostGIS extension...'

-- Create PostGIS extension in the main database
\c :POSTGRES_DB;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

\echo 'PostGIS extension created for main database'

-- Create test database and enable PostGIS
CREATE DATABASE gef_test;
\c gef_test;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

\echo 'PostGIS extension created for test database'

-- Grant permissions
\c :POSTGRES_DB;
GRANT ALL PRIVILEGES ON DATABASE gef_test TO :POSTGRES_USER;

\echo 'PostGIS initialization complete'