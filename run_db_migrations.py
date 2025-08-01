#!/usr/bin/env python3
"""
Database migration script - Simplified version
"""

import atexit
import logging
import sys
import time

# Add the project root to Python path
sys.path.insert(0, "/opt/gef-api")

# Set up logging with more verbose output
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def cleanup():
    logger.info("Script is exiting...")
    sys.stdout.flush()
    sys.stderr.flush()


# Register cleanup function
atexit.register(cleanup)


def wait_for_database():
    """Wait for database to be ready"""
    logger.info("Waiting for database to be ready...")

    max_retries = 30
    retry_count = 0

    while retry_count < max_retries:
        try:
            from sqlalchemy import text

            from gefapi import db

            # Test database connection
            with db.engine.connect() as connection:
                connection.execute(text("SELECT 1")).fetchone()

            logger.info("Database is ready!")
            return True

        except Exception as e:
            retry_count += 1
            logger.info(
                f"Database not ready (attempt {retry_count}/{max_retries}): {e}"
            )
            time.sleep(2)

    raise RuntimeError("Database did not become ready within timeout period")


def run_migrations():
    """Run database migrations"""
    print("Running database migrations...")
    logger.info("Migration script started")

    try:
        # Wait for database to be ready
        wait_for_database()

        logger.info("Importing Flask-Migrate...")
        from flask_migrate import upgrade

        logger.info("Importing gefapi app...")
        from gefapi import app

        logger.info("Imports completed successfully")

        print("Creating Flask app context...")
        logger.info("Creating Flask app context...")

        with app.app_context():
            logger.info("App context created successfully")

            # Simple approach: just run the standard upgrade to 'head'
            # This is equivalent to running 'flask db upgrade' manually
            logger.info("Running Flask-Migrate upgrade to head...")
            print("Running upgrade to head...")

            upgrade(revision="head")

            logger.info("Flask-Migrate upgrade completed successfully")
            print("✓ Database migrations completed successfully")

    except Exception as e:
        print(f"✗ Migration failed: {e}")
        logger.error(f"Migration failed: {e}")
        import traceback

        traceback.print_exc()
        logger.error("Full traceback printed above")
        sys.exit(1)

    logger.info("Migration script completed successfully")


def setup_staging_environment():
    """Set up complete staging environment including users, scripts, and logs"""
    import os

    # Only run in staging environment
    if os.getenv("ENVIRONMENT") != "staging":
        logger.info("Not in staging environment, skipping staging setup")
        return

    logger.info("Setting up complete staging environment...")

    try:
        # Import the comprehensive setup module
        import sys

        sys.path.insert(0, "/opt/gef-api")

        # Run the comprehensive staging setup
        from setup_staging_environment import StagingEnvironmentSetup

        setup = StagingEnvironmentSetup()
        setup.run()

        logger.info("Staging environment setup completed successfully")
        print("✓ Staging environment setup completed successfully")

    except Exception as e:
        logger.error(f"Failed to setup staging environment: {e}")
        print(f"✗ Staging environment setup failed: {e}")
        # Don't fail the migration for staging setup issues
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    run_migrations()
    setup_staging_environment()


def setup_staging_environment():
    """Set up complete staging environment including users, scripts, and logs"""
    import os

    # Only run in staging environment
    if os.getenv("ENVIRONMENT") != "staging":
        logger.info("Not in staging environment, skipping staging setup")
        return

    logger.info("Setting up complete staging environment...")

    try:
        # Import the comprehensive setup module
        import sys

        sys.path.insert(0, "/opt/gef-api")

        # Run the comprehensive staging setup
        from setup_staging_environment import StagingEnvironmentSetup

        setup = StagingEnvironmentSetup()
        setup.run()

        logger.info("Staging environment setup completed successfully")
        print("✓ Staging environment setup completed successfully")

    except Exception as e:
        logger.error(f"Failed to setup staging environment: {e}")
        print(f"✗ Staging environment setup failed: {e}")
        # Don't fail the migration for staging setup issues
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    run_migrations()
    setup_staging_environment()
