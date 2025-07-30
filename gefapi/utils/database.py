"""Database utility functions and decorators."""

from functools import wraps
import logging
import time

from sqlalchemy.exc import DisconnectionError, OperationalError

logger = logging.getLogger(__name__)


def retry_db_operation(max_retries=3, backoff_seconds=1):
    """
    Decorator to retry database operations on connection failures.

    This decorator automatically retries database operations when they fail due to
    connection issues like server disconnections, network timeouts, or stale
    connections. It uses exponential backoff and automatically disposes of the
    connection pool when connection errors are detected.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        backoff_seconds: Initial backoff time in seconds, doubles with each retry
                        (default: 1)

    Returns:
        Decorated function that will retry on database connection failures

    Example:
        @retry_db_operation(max_retries=3, backoff_seconds=2)
        def query_users():
            return db.session.query(User).all()

    Note:
        This decorator requires the Flask app context to be active and assumes
        the availability of a global `db` object (typically Flask-SQLAlchemy).
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Import db here to avoid circular imports
            from gefapi import db

            last_exception = None
            backoff = backoff_seconds

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (OperationalError, DisconnectionError) as e:
                    last_exception = e

                    # Check if this is a connection-related error
                    error_msg = str(e).lower()
                    connection_errors = [
                        "server closed the connection unexpectedly",
                        "connection was closed",
                        "connection is closed",
                        "lost connection",
                        "connection reset by peer",
                        "broken pipe",
                        "network is unreachable",
                        "connection timed out",
                        "no connection to the server",
                        "could not connect to server",
                    ]

                    is_connection_error = any(
                        err in error_msg for err in connection_errors
                    )

                    if not is_connection_error or attempt == max_retries:
                        # Not a connection error or final attempt - re-raise
                        raise e

                    logger.warning(
                        f"Database connection error on attempt {attempt + 1}/"
                        f"{max_retries + 1}: {e}. Retrying in {backoff} seconds..."
                    )

                    # Try to rollback and dispose of the current connection
                    try:
                        db.session.rollback()
                        db.engine.dispose()
                        logger.info("Database connection pool refreshed")
                    except Exception as cleanup_error:
                        logger.warning(
                            f"Error during connection cleanup: {cleanup_error}"
                        )

                    if attempt < max_retries:
                        time.sleep(backoff)
                        backoff *= 2  # Exponential backoff

            # This should never be reached, but just in case
            raise last_exception

        return wrapper

    return decorator


def test_database_connection():
    """
    Test database connectivity with a simple query.

    Returns:
        bool: True if connection is working, False otherwise

    Example:
        if not test_database_connection():
            logger.error("Database is not accessible")
            # Handle accordingly
    """
    try:
        # Import db here to avoid circular imports
        from gefapi import db

        # Simple query to test connection
        db.session.execute("SELECT 1").fetchone()
        return True
    except Exception as e:
        logger.warning(f"Database connection test failed: {e}")
        return False


def ensure_db_connection():
    """
    Ensure database connection is healthy, disposing and reconnecting if needed.

    This function tests the database connection and automatically disposes of
    the connection pool if the connection is stale or broken.

    Returns:
        bool: True if connection is healthy after cleanup, False otherwise

    Example:
        if not ensure_db_connection():
            raise RuntimeError("Could not establish database connection")
    """
    from gefapi import db

    # Test initial connection
    if test_database_connection():
        return True

    logger.info("Database connection unhealthy, disposing connection pool")

    try:
        # Dispose of current connections
        db.session.rollback()
        db.engine.dispose()
        logger.info("Connection pool disposed, testing new connection")

        # Test again with fresh connection
        return test_database_connection()
    except Exception as e:
        logger.error(f"Error during connection recovery: {e}")
        return False


def with_db_retry(func):
    """
    Simplified decorator that applies standard retry logic to a function.

    This is a convenience decorator that applies retry_db_operation with
    sensible defaults (3 retries, 2 second initial backoff).

    Example:
        @with_db_retry
        def get_user_count():
            return db.session.query(func.count(User.id)).scalar()
    """
    return retry_db_operation(max_retries=3, backoff_seconds=2)(func)
