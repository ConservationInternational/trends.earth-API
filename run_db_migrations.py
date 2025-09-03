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


def wait_for_database(app):
    """Wait for database to be ready"""
    logger.info("Waiting for database to be ready...")

    max_retries = 30
    retry_count = 0

    while retry_count < max_retries:
        try:
            from sqlalchemy import text

            from gefapi import db

            # Test database connection within app context
            with app.app_context(), db.engine.connect() as connection:
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
        logger.info("Importing Flask-Migrate...")
        from alembic.script import ScriptDirectory
        from flask_migrate import upgrade

        logger.info("Importing gefapi app...")
        from gefapi import app

        logger.info("Imports completed successfully")

        # Wait for database to be ready
        wait_for_database(app)

        print("Creating Flask app context...")
        logger.info("Creating Flask app context...")

        with app.app_context():
            logger.info("App context created successfully")

            # Check for multiple heads before attempting upgrade
            logger.info("Checking migration heads...")

            try:
                # Get Flask-Migrate config
                from flask_migrate import Migrate

                migrate = Migrate()
                migrate.init_app(app, app.extensions["migrate"].db)

                # Get Alembic config from Flask-Migrate
                config = migrate.get_config()
                script = ScriptDirectory.from_config(config)

                # Get current heads
                heads = script.get_heads()
                logger.info(f"Found {len(heads)} migration heads: {heads}")

                if len(heads) > 1:
                    logger.warning(f"Multiple heads detected: {heads}")
                    print(f"⚠️  Multiple migration heads detected: {heads}")

                    # Try to upgrade to each head individually to find current state
                    logger.info(
                        "Attempt to fix multiple heads by upgrading to merged head..."
                    )
                    print("🔧 Resolving multiple heads...")

                    # Use the merge migration that combines both branches
                    target_head = "merge_st_be_2025"
                    logger.info(f"Upgrading to merged head: {target_head}")
                    upgrade(revision=target_head)

                else:
                    logger.info("Single head found, proceeding with normal upgrade")
                    upgrade(revision="head")

            except Exception as head_check_error:
                logger.warning(f"Head check failed: {head_check_error}")
                print(
                    f"⚠️  Head check failed, trying direct upgrade: {head_check_error}"
                )

                # Fallback: try direct upgrade to head
                try:
                    upgrade(revision="head")
                except Exception as upgrade_error:
                    logger.error(f"Direct upgrade failed: {upgrade_error}")

                    # Final fallback: try upgrading to merged head
                    logger.info("Trying upgrade to merged head as final fallback...")
                    upgrade(revision="merge_st_be_2025")

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
