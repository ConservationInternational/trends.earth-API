#!/bin/bash
set -e

# Handle Docker socket permissions for non-root user in Swarm
# This approach works across different Swarm nodes with different group configurations
if [ -e /var/run/docker.sock ]; then
    # Get the group ID of the Docker socket
    DOCKER_SOCK_GID=$(stat -c %g /var/run/docker.sock 2>/dev/null || echo "999")
    echo "Docker socket found with GID: $DOCKER_SOCK_GID"
    
    # Get the expected docker group GID from environment
    EXPECTED_DOCKER_GID=${DOCKER_GROUP_ID:-999}
    echo "Expected Docker group GID from environment: $EXPECTED_DOCKER_GID"
    
    # Get current docker group GID in container
    CURRENT_DOCKER_GID=$(getent group docker | cut -d: -f3 2>/dev/null || echo "999")
    echo "Current container docker group GID: $CURRENT_DOCKER_GID"
    
    # Check if user can access the socket
    if [ -w /var/run/docker.sock ]; then
        echo "✅ Docker socket is accessible to current user"
    else
        echo "❌ Docker socket is not writable by current user"
        
        # Try to fix permissions by adding user to the socket's group
        # This requires the container to run with appropriate capabilities
        echo "Attempting to fix Docker socket permissions..."
        
        # Method 1: Try to create/modify docker group to match socket GID
        if [ "$DOCKER_SOCK_GID" != "$CURRENT_DOCKER_GID" ]; then
            echo "Adjusting docker group GID from $CURRENT_DOCKER_GID to $DOCKER_SOCK_GID"
            
            # This will only work if container has appropriate privileges
            if command -v groupmod >/dev/null 2>&1; then
                if groupmod -g "$DOCKER_SOCK_GID" docker 2>/dev/null; then
                    echo "✅ Successfully updated docker group GID"
                else
                    echo "⚠️ Could not update docker group GID (insufficient privileges)"
                fi
            fi
        fi
        
        # Verify access again
        if [ -w /var/run/docker.sock ]; then
            echo "✅ Docker socket access fixed"
        else
            echo "❌ Docker socket still not accessible"
            echo "Current user: $(whoami) ($(id))"
            echo "Socket permissions: $(ls -la /var/run/docker.sock 2>/dev/null || echo 'Cannot stat socket')"
            echo "User groups: $(groups)"
            echo ""
            echo "🔧 To fix this issue:"
            echo "1. Ensure DOCKER_GROUP_ID environment variable matches the host docker group:"
            echo "   Host: getent group docker | cut -d: -f3"
            echo "   Container: $EXPECTED_DOCKER_GID"
            echo "2. Or run container with additional privileges to modify groups"
            echo "3. Or ensure all Swarm nodes have consistent docker group GIDs"
        fi
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
