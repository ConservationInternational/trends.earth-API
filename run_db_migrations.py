#!/usr/bin/env python3
"""
Database migration script
"""

import atexit
import logging
import sys

# Add the project root to Python path
sys.path.insert(0, "/opt/gef-api")

# Set up logging with more verbose output
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def cleanup():
    logger.info("Script is exiting...")
    sys.stdout.flush()
    sys.stderr.flush()


# Register cleanup function
atexit.register(cleanup)


def run_migrations():
    """Run database migrations"""
    print("Running database migrations...")
    logger.info("Migration script started")

    try:
        logger.info("Importing Flask-Migrate...")
        from flask_migrate import upgrade

        logger.info("Importing gefapi app...")
        from gefapi import app

        logger.info("Imports completed successfully")

        # Import database related modules
        from sqlalchemy import text
        from sqlalchemy.exc import ProgrammingError

        from gefapi import db

        print("Creating Flask app context...")
        logger.info("Creating Flask app context...")

        with app.app_context():
            print("App context created, starting migration upgrade...")
            logger.info("App context created successfully")

            # First, check if we can connect to the database at all
            logger.info("Testing basic database connectivity...")
            try:
                # Create a completely fresh connection for testing
                connection = db.engine.connect()
                test_result = connection.execute(text("SELECT 1")).fetchone()
                logger.info(f"Database connectivity test successful: {test_result[0]}")
                connection.close()
            except Exception as db_error:
                logger.error(f"Database connectivity test failed: {db_error}")
                raise RuntimeError(
                    f"Cannot connect to database: {db_error}"
                ) from db_error

            # Check current migration state with proper error handling
            logger.info("Checking current migration state...")
            current_version = None
            try:
                # Use a fresh connection for migration state check
                connection = db.engine.connect()
                result = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
                current_version = result[0] if result else "None"
                logger.info(f"Current database version: {current_version}")
                connection.close()
            except ProgrammingError as e:
                if 'relation "alembic_version" does not exist' in str(e):
                    logger.info(
                        "Database appears to be fresh (no alembic_version table)"
                    )
                    current_version = None
                else:
                    logger.warning(f"Could not check current version: {e}")
                    current_version = None
            except Exception as e:
                logger.warning(f"Could not check current version: {e}")
                current_version = None

            logger.info("Starting Flask-Migrate upgrade...")
            print("About to call upgrade()...")

            # First check what columns exist to determine the right target
            # Use a fresh connection for each query to avoid transaction issues
            existing_branch2 = []
            existing_status_log = []
            refresh_tokens_exists = False

            try:
                connection = db.engine.connect()
                result = connection.execute(
                    text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'script'
                    AND column_name IN (
                        'cpu_reservation', 'cpu_limit',
                        'memory_reservation', 'memory_limit'
                    )
                """)
                ).fetchall()
                existing_branch2 = [row[0] for row in result]
                connection.close()
            except Exception as e:
                logger.warning(f"Could not check script table columns: {e}")

            try:
                connection = db.engine.connect()
                status_log_result = connection.execute(
                    text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'status_log'
                    AND column_name IN ('executions_failed', 'executions_count')
                """)
                ).fetchall()
                existing_status_log = [row[0] for row in status_log_result]
                connection.close()
            except Exception as e:
                logger.warning(f"Could not check status_log table columns: {e}")

            # Check if refresh tokens table exists
            try:
                connection = db.engine.connect()
                refresh_tokens_result = connection.execute(
                    text("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_name = 'refresh_tokens'
                """)
                ).fetchall()
                refresh_tokens_exists = len(refresh_tokens_result) > 0
                connection.close()
            except Exception as e:
                logger.warning(f"Could not check refresh_tokens table existence: {e}")

            logger.info(f"Branch 2 columns in script table: {existing_branch2}")
            logger.info(f"Status log columns: {existing_status_log}")
            logger.info(f"Refresh tokens table exists: {refresh_tokens_exists}")

            if len(existing_branch2) >= 4:
                # Branch 2 is already applied, check what else needs to be done
                if len(existing_status_log) == 0:
                    logger.info(
                        "Branch 2 already applied, targeting g23bc4de5678 "
                        "for status_log columns"
                    )
                    upgrade(revision="g23bc4de5678")
                elif not refresh_tokens_exists:
                    logger.info(
                        "Status log already applied, adding refresh tokens table"
                    )
                    upgrade(revision="add_refresh_tokens")
                else:
                    logger.info("All migrations already applied")
                    print("✓ Database migrations already completed")
                    return
            elif current_version is None:
                # Fresh database - run all migrations
                logger.info("Fresh database detected, running all migrations")
                # Since we have multiple heads, we need to upgrade to the merge point
                # first then to the latest migration
                try:
                    logger.info("First upgrading to merge point h34de5fg6789")
                    upgrade(revision="h34de5fg6789")
                    logger.info("Now upgrading to add refresh tokens")
                    upgrade(revision="add_refresh_tokens")
                except Exception as e:
                    logger.warning(
                        f"Merge upgrade failed, trying direct upgrade to heads: {e}"
                    )
                    # If merge fails, try upgrading to all heads
                    upgrade(revision="heads")
            else:
                # Need to apply merge migration
                logger.info("Applying merge migration h34de5fg6789")
                upgrade(revision="h34de5fg6789")

            logger.info("Flask-Migrate upgrade completed successfully")
            print("✓ Database migrations completed successfully")

            # Check final migration state
            try:
                connection = db.engine.connect()
                result = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
                final_version = result[0] if result else "None"
                logger.info(f"Final database version: {final_version}")
                connection.close()
            except Exception as e:
                logger.warning(f"Could not check final version: {e}")

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
    if os.getenv('ENVIRONMENT') != 'staging':
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
