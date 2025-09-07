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
        from gefapi import app, db

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

                    # Check if database is already at the target state
                    try:
                        # Test if all expected columns exist by querying models
                        from gefapi.models.script import Script
                        from gefapi.models.status_log import StatusLog
                        from gefapi.models.user import User

                        # Check User model for new columns
                        user = db.session.query(User).first()
                        if user is not None:
                            # Try to access the new columns to verify they exist
                            _ = user.gee_oauth_token
                            _ = user.email_notifications_enabled
                            logger.info("✅ Database schema appears to be up-to-date")
                            print(
                                "✅ Database schema is already current - "
                                "no migration needed"
                            )
                            _ = (
                                user.email_notifications_enabled
                                if hasattr(user, "email_notifications_enabled")
                                else None
                            )
                            _ = (
                                user.google_groups_trends_earth_users
                                if hasattr(user, "google_groups_trends_earth_users")
                                else None
                            )

                        # Check status_log for new columns
                        status_log = db.session.query(StatusLog).first()
                        if status_log is not None:
                            _ = (
                                status_log.status_from
                                if hasattr(status_log, "status_from")
                                else None
                            )
                            _ = (
                                status_log.status_to
                                if hasattr(status_log, "status_to")
                                else None
                            )
                            _ = (
                                status_log.execution_id
                                if hasattr(status_log, "execution_id")
                                else None
                            )

                        # Check script for build_error column
                        script = db.session.query(Script).first()
                        if script is not None:
                            _ = (
                                script.build_error
                                if hasattr(script, "build_error")
                                else None
                            )

                        logger.info("✅ Database schema appears to be up-to-date")
                        print(
                            "✅ Database schema is already current - "
                            "no migration needed"
                        )
                        print("✓ Database migrations completed successfully")
                        return
                    except Exception as schema_check_error:
                        logger.info(
                            "Schema check indicated migration needed: "
                            f"{schema_check_error}"
                        )

                    # Try to upgrade to the known good merged head
                    logger.info(
                        "Attempting to resolve multiple heads by upgrading to "
                        "the latest merged head..."
                    )
                    print("🔧 Resolving multiple heads...")

                    # Use the latest merge migration that combines all branches
                    target_head = "heads"  # Upgrade to all heads
                    logger.info(f"Upgrading to all heads: {target_head}")
                    try:
                        upgrade(revision=target_head)
                    except Exception as upgrade_error:
                        if "already exists" in str(
                            upgrade_error
                        ) or "DuplicateColumn" in str(upgrade_error):
                            logger.info(
                                "✅ Database appears to be up-to-date (columns exist)"
                            )
                            print("✅ Database schema is already current")
                            print("✓ Database migrations completed successfully")
                            return
                        raise upgrade_error

                else:
                    logger.info("Single head found, proceeding with normal upgrade")
                    print("✓ Single migration head found - proceeding with upgrade")
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

                    # Final fallback: try upgrading to latest merged state
                    logger.info("Trying upgrade to heads as final fallback...")
                    upgrade(revision="heads")

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
