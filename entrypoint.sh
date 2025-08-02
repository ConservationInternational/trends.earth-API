#!/bin/bash
set -e

# Docker socket check - only needed for services that require Docker access
check_docker_access() {
    if [ -e /var/run/docker.sock ]; then
        if [ -w /var/run/docker.sock ]; then
            echo "✅ Docker socket is accessible"
        else
            echo "⚠️ Docker socket is not writable"
            echo "Socket permissions: $(ls -la /var/run/docker.sock)"
        fi
    else
        echo "⚠️ Docker socket not found at /var/run/docker.sock"
        echo "Container will not be able to execute Docker operations"
    fi
}

# Only check Docker access for services that need it
if [ "$1" = "worker" ] && [ "$CELERY_WORKER_QUEUES" = "build" ]; then
    echo "Build worker detected - checking Docker access..."
    check_docker_access
elif [ "$1" = "worker" ] && [ -z "$CELERY_WORKER_QUEUES" ]; then
    # Default worker in development might need Docker access
    if [ "$ENVIRONMENT" = "dev" ]; then
        echo "Development worker - checking Docker access..."
        check_docker_access
    fi
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
        
        # Get database configuration from environment
        DB_USER=${POSTGRES_USER:-trendsearth_develop}
        DB_PASSWORD=${POSTGRES_PASSWORD:-postgres}
        DB_HOST=${POSTGRES_HOST:-postgres}
        DB_NAME=${POSTGRES_DB:-trendsearth_develop_db}
        
        # Wait for database to be ready
        echo "Waiting for database to be ready..."
        until PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c '\l' >/dev/null 2>&1; do
            echo "Database is unavailable - sleeping"
            sleep 2
        done
        echo "Database is ready!"
        
        # Create test database if it doesn't exist
        echo "Creating test database if needed..."
        PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c "CREATE DATABASE gef_test;" 2>/dev/null || echo "Test database already exists"
        
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
        # Check for specific queue configuration
        if [ -n "$CELERY_WORKER_QUEUES" ]; then
            echo "Starting worker with queues: $CELERY_WORKER_QUEUES"
            exec celery -A gefapi.celery worker -Q "$CELERY_WORKER_QUEUES" -E --loglevel=DEBUG
        else
            echo "Starting worker with default queues"
            exec celery -A gefapi.celery worker -E --loglevel=DEBUG
        fi
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
