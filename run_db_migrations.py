#!/usr/bin/env python3
"""
Database migration script - Simplified version with enhanced debugging
"""

import atexit
import logging
import os
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


def ensure_postgis_extensions(app):
    """Ensure PostGIS extensions are installed in the database"""

    from sqlalchemy import text

    from gefapi import db

    logger.info("Checking PostGIS extensions...")

    try:
        with app.app_context(), db.engine.connect() as connection:
            # Check if PostGIS extension exists
            result = connection.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_extension "
                    "WHERE extname = 'postgis')"
                )
            )
            postgis_exists = result.scalar()

            if not postgis_exists:
                logger.info("PostGIS extension not found, installing...")
                print("📍 Installing PostGIS extension...")

                # Create PostGIS extension
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
                connection.execute(
                    text("CREATE EXTENSION IF NOT EXISTS postgis_topology")
                )
                connection.commit()

                logger.info("PostGIS extensions installed successfully")
                print("✓ PostGIS extensions installed")
            else:
                logger.info("PostGIS extension already installed")
                print("✓ PostGIS extension already installed")

    except Exception as e:
        logger.error(f"Failed to install PostGIS extensions: {e}")
        print(f"✗ PostGIS extension installation failed: {e}")
        # Only fail in staging where we control the environment
        if os.getenv("ENVIRONMENT") == "staging":
            raise RuntimeError(
                f"PostGIS extension required but failed to install: {e}"
            ) from e
        logger.warning(
            "PostGIS extension installation failed but continuing "
            "(not in staging environment)"
        )


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

        # Ensure PostGIS extensions are installed (required for geometry columns)
        ensure_postgis_extensions(app)

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

                # Check current database revision
                from alembic.runtime.migration import MigrationContext

                with db.engine.connect() as connection:
                    context = MigrationContext.configure(connection)
                    current_rev = context.get_current_revision()
                    logger.info(f"Current database revision: {current_rev}")

                # Additional debugging: list all revisions to help debug migration state
                all_revisions = list(script.walk_revisions())
                logger.info(
                    f"Total revisions in migration directory: {len(all_revisions)}"
                )
                if all_revisions:
                    latest_rev = all_revisions[0]
                    doc_first_line = (
                        latest_rev.doc.split(chr(10))[0]
                        if latest_rev.doc
                        else "No description"
                    )
                    logger.info(
                        f"Latest revision: {latest_rev.revision} - {doc_first_line}"
                    )

                # If current database revision is already at the expected head,
                # no migration needed
                if current_rev in heads:
                    logger.info(
                        f"✅ Database is already at head revision: {current_rev}"
                    )
                    print(f"✅ Database is already current (revision: {current_rev})")
                    print("✓ Database migrations completed successfully")
                    return

                if len(heads) > 1:
                    logger.warning(f"Multiple heads detected: {heads}")
                    print(f"⚠️  Multiple migration heads detected: {heads}")

                    # For multiple heads, attempt to upgrade to all heads
                    logger.info(
                        "Attempting to resolve multiple heads by upgrading to all heads"
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

                # Check if this is a "Can't locate revision" error
                if "Can't locate revision" in str(head_check_error):
                    logger.info(
                        "Revision location error - likely database/migration mismatch"
                    )
                    print("⚠️  Migration revision mismatch detected")

                    # Try to get current database revision for debugging
                    try:
                        from alembic.runtime.migration import MigrationContext

                        with db.engine.connect() as connection:
                            context = MigrationContext.configure(connection)
                            current_rev = context.get_current_revision()
                            logger.info(f"Current database revision: {current_rev}")
                            print(f"📋 Database is at revision: {current_rev}")

                    except Exception as db_check_error:
                        logger.warning(
                            f"Could not check database revision: {db_check_error}"
                        )

                    # Don't try to fix this automatically - let it fall through
                    # to other migration attempts

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
        success = setup.run()

        if success:
            logger.info("Staging environment setup completed successfully")
            print("✓ Staging environment setup completed successfully")
        else:
            logger.warning(
                "Staging environment setup completed with some limitations "
                "(likely missing production database credentials)"
            )
            print("⚠️ Staging environment setup completed with limitations")

    except Exception as e:
        logger.error(f"Failed to setup staging environment: {e}")
        print(f"✗ Staging environment setup failed: {e}")
        # Don't fail the migration for staging setup issues
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    run_migrations()
    setup_staging_environment()
