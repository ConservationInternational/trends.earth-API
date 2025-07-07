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

set -e

echo "Starting necessary services..."
docker compose -f docker-compose.develop.yml up -d database redis

echo "Waiting for services to be ready..."
sleep 5

echo "Creating test database if it doesn't exist..."
docker compose -f docker-compose.develop.yml exec -T database psql -U root -d postgres -c "CREATE DATABASE gef_test;" 2>/dev/null || echo "Test database already exists"

echo "Running tests..."
if [ $# -eq 0 ]; then
    echo "No arguments provided, running all tests..."
    docker compose -f docker-compose.develop.yml run --rm test
else
    echo "Running with arguments: $@"
    docker compose -f docker-compose.develop.yml run --rm test python -m pytest "$@"
fi

echo "Stopping services..."
docker compose -f docker-compose.develop.yml down
