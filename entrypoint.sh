#!/bin/bash
set -e

# Only chown if the file exists (prevents errors if not mounted)
if [ -e /tmp/docker.sock ]; then
    chown $USER:$USER /tmp/docker.sock
fi

case "$1" in
    develop)
        echo "Running Development Server"
        exec python main.py
        ;;
    test)
        echo "Running tests"
        export TESTING=true
        
        # Create test database if it doesn't exist
        echo "Creating test database if needed..."
        PGPASSWORD=root psql -h database -U root -d postgres -c "CREATE DATABASE gef_test;" 2>/dev/null || echo "Test database already exists"
        
        # Skip the first argument (which is "test") and pass the rest to pytest
        shift
        if [ $# -eq 0 ]; then
            # No additional arguments, run all tests
            echo "Starting pytest..."
            python -m pytest --no-cov -x
            echo "Pytest finished with exit code: $?"
            exit 0
        else
            # Additional arguments provided, pass them to pytest
            echo "Starting pytest with args: $@"
            python -m pytest --no-cov -x "$@"
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
