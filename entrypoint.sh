#!/bin/bash
set -e

# Simple Docker socket check - containers now run as root for guaranteed access
if [ -e /var/run/docker.sock ]; then
    if [ -w /var/run/docker.sock ]; then
        echo "✅ Docker socket is accessible"
    else
        echo "⚠️ Docker socket is not writable (this should not happen with root user)"
        echo "Socket permissions: $(ls -la /var/run/docker.sock)"
    fi
else
    echo "⚠️ Docker socket not found at /var/run/docker.sock"
    echo "Container will not be able to execute scripts that require Docker"
fi

echo "---"

case "$1" in
    develop)
        echo "Running Development Server"
        exec python main.py
        ;;
    test)
        echo "Running tests"
        export TESTING=true
        
        # Wait for database to be ready
        echo "Waiting for database to be ready..."
        until PGPASSWORD=root psql -h database -U root -d postgres -c '\l' >/dev/null 2>&1; do
            echo "Database is unavailable - sleeping"
            sleep 2
        done
        echo "Database is ready!"
        
        # Create test database if it doesn't exist
        echo "Creating test database if needed..."
        PGPASSWORD=root psql -h database -U root -d postgres -c "CREATE DATABASE gef_test;" 2>/dev/null || echo "Test database already exists"
        
        # Skip the first argument (which is "test") and pass the rest to pytest
        shift
        if [ $# -eq 0 ]; then
            # No additional arguments, run all tests
            echo "Starting pytest..."
            python -m pytest --no-cov
            echo "Pytest finished with exit code: $?"
            exit 0
        else
            # Additional arguments provided, pass them to pytest
            echo "Starting pytest with args: $@"
            python -m pytest --no-cov "$@"
            echo "Pytest finished with exit code: $?"
            exit 0
        fi
        ;;
    start)
        echo "Running Start"
        exec gunicorn -c gunicorn.py gefapi.wsgi:application
        ;;
    worker)
        echo "Running celery"
        exec celery -A gefapi.celery worker -E --loglevel=DEBUG
        ;;
    beat)
        echo "Running celery beat"
        exec celery -A gefapi.celery beat --loglevel=DEBUG
        ;;
    migrate)
        echo "Running database migrations"
        exec python run_db_migrations.py
        ;;
    *)
        exec "$@"
esac
