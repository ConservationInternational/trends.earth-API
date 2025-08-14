#!/bin/bash

# Script to run tests in Docker environment
# This ensures the database and redis services are running before tests
#
# Usage:
#   ./run_tests.sh                                                          # Run all tests
#   ./run_tests.sh tests/test_integration.py                               # Run all tests in a file
#   ./run_tests.sh tests/test_integration.py::TestAPIIntegration            # Run all tests in a class
#   ./run_tests.sh tests/test_integration.py::TestAPIIntegration::test_admin_management_workflow  # Run specific test
#   ./run_tests.sh -v --no-cov tests/test_integration.py                   # Run with pytest options
#   ./run_tests.sh -x                                                      # Stop on first failure


PYTEST_ARGS=()
STOP_ON_FAIL=""
RESET_DB="0"

# Parse arguments for --reset-db and -x
for arg in "$@"; do
    if [[ "$arg" == "--reset-db" ]]; then
        RESET_DB="1"
    elif [[ "$arg" == "-x" ]]; then
        STOP_ON_FAIL="--exitfirst"
        PYTEST_ARGS+=("$arg")
    else
        PYTEST_ARGS+=("$arg")
    fi
done

echo "Starting necessary services..."
docker compose -f docker-compose.develop.yml up -d postgres redis

echo "Waiting for services to be ready..."
sleep 5

# Get database configuration from environment
DB_USER=${POSTGRES_USER:-trendsearth_develop}
DB_PASSWORD=${POSTGRES_PASSWORD:-postgres}
DB_NAME=${POSTGRES_DB:-trendsearth_develop_db}

echo "Creating test database if it doesn't exist..."
docker compose -f docker-compose.develop.yml exec -T postgres env PGPASSWORD="$DB_PASSWORD" psql -U "$DB_USER" -d "$DB_NAME" -c "CREATE DATABASE gef_test;" 2>/dev/null || echo "Test database already exists"

echo "Running tests..."


# Optionally drop and recreate the test database if --reset-db flag is set
if [ "$RESET_DB" = "1" ]; then
    echo "Dropping and recreating test database (--reset-db flag)..."
    docker compose -f docker-compose.develop.yml exec -T postgres env PGPASSWORD="$DB_PASSWORD" psql -U "$DB_USER" -d "$DB_NAME" -c "DROP DATABASE IF EXISTS gef_test;"
    docker compose -f docker-compose.develop.yml exec -T postgres env PGPASSWORD="$DB_PASSWORD" psql -U "$DB_USER" -d "$DB_NAME" -c "CREATE DATABASE gef_test;"
fi

if [ ${#PYTEST_ARGS[@]} -eq 0 ]; then
    echo "No arguments provided, running all tests..."
    docker compose -f docker-compose.develop.yml run --rm test
else
    echo "Running with arguments: ${PYTEST_ARGS[*]}"
    docker compose -f docker-compose.develop.yml run --rm test python -m pytest "${PYTEST_ARGS[@]}" $STOP_ON_FAIL
fi

echo "Stopping services..."
docker compose -f docker-compose.develop.yml down
