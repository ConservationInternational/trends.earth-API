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
        echo "Test (not yet)"
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
    *)
        exec "$@"
esac
