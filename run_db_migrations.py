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

        print("Creating Flask app context...")
        logger.info("Creating Flask app context...")

        with app.app_context():
            print("App context created, starting migration upgrade...")
            logger.info("App context created successfully")

            # Check current migration state before running
            logger.info("Checking current migration state...")
            from sqlalchemy import text

            from gefapi import db

            try:
                result = db.session.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
                current_version = result[0] if result else "None"
                logger.info(f"Current database version: {current_version}")
            except Exception as e:
                logger.warning(f"Could not check current version: {e}")

            # Test database connectivity first
            logger.info("Testing database connectivity...")
            try:
                test_result = db.session.execute(text("SELECT 1")).fetchone()
                logger.info(f"Database connectivity test successful: {test_result[0]}")
            except Exception as db_error:
                logger.error(f"Database connectivity test failed: {db_error}")
                raise RuntimeError(
                    f"Cannot connect to database: {db_error}"
                ) from db_error

            logger.info("Starting Flask-Migrate upgrade...")
            print("About to call upgrade()...")

            # First check what columns exist to determine the right target
            result = db.session.execute(
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

            status_log_result = db.session.execute(
                text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'status_log'
                AND column_name IN ('executions_failed', 'executions_count')
            """)
            ).fetchall()
            existing_status_log = [row[0] for row in status_log_result]

            logger.info(f"Branch 2 columns in script table: {existing_branch2}")
            logger.info(f"Status log columns: {existing_status_log}")

            if len(existing_branch2) >= 4:
                # Branch 2 is already applied, just need to add status_log columns
                if len(existing_status_log) == 0:
                    logger.info(
                        "Branch 2 already applied, targeting g23bc4de5678 "
                        "for status_log columns"
                    )
                    upgrade(revision="g23bc4de5678")
                else:
                    logger.info("Both branches already applied")
                    print("✓ Database migrations already completed")
                    return
            else:
                # Need to apply merge migration
                logger.info("Applying merge migration h34de5fg6789")
                upgrade(revision="h34de5fg6789")

            logger.info("Flask-Migrate upgrade completed successfully")
            print("✓ Database migrations completed successfully")

            # Check final migration state
            try:
                result = db.session.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
                final_version = result[0] if result else "None"
                logger.info(f"Final database version: {final_version}")
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


if __name__ == "__main__":
    run_migrations()
