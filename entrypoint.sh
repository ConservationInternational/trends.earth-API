#!/bin/bash
set -e

# Handle Docker socket permissions for non-root user
# This approach is more secure than running the entire container as root
if [ -e /tmp/docker.sock ]; then
    # Get the group ID of the Docker socket
    DOCKER_SOCK_GID=$(stat -c %g /tmp/docker.sock 2>/dev/null || echo "999")
    
    # Check if we need to adjust group permissions
    if ! groups | grep -q docker; then
        echo "Warning: User not in docker group, Docker operations may fail"
        echo "Docker socket GID: $DOCKER_SOCK_GID"
        echo "Current user groups: $(groups)"
    fi
    
    # Ensure the socket is readable by the docker group
    if [ -w /tmp/docker.sock ] || [ "$(stat -c %G /tmp/docker.sock 2>/dev/null)" = "docker" ]; then
        echo "Docker socket accessible to user"
    else
        echo "Warning: Docker socket may not be accessible - check host docker group setup"
    fi
fi

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
