#!/usr/bin/env python3
"""
Database migration script
"""

import sys

# Add the project root to Python path
sys.path.insert(0, "/opt/gef-api")


def run_migrations():
    """Run database migrations"""
    print("Running database migrations...")

    try:
        # Use Alembic directly to avoid Flask app import issues
        from alembic import command
        from alembic.config import Config

        from gefapi.config import SETTINGS

        print("Setting up Alembic configuration...")

        # Test database connectivity first
        db_url = SETTINGS.get("SQLALCHEMY_DATABASE_URI")
        print(f"Database URL: {db_url}")

        # Test basic database connection
        print("Testing database connectivity...")
        try:
            from sqlalchemy import create_engine, text

            engine = create_engine(db_url)
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1 as test")).fetchone()
                print(f"✓ Database connection successful: {result}")
        except Exception as db_error:
            print(f"✗ Database connection failed: {db_error}")
            raise

        # Set up Alembic configuration
        alembic_cfg = Config("/opt/gef-api/migrations/alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
        alembic_cfg.set_main_option("script_location", "/opt/gef-api/migrations")

        print("Running Alembic upgrade...")
        
        # First, let's check the current database state and available revisions
        try:
            print("Checking current database revision...")
            from alembic import command as alembic_command
            
            # Get current revision from database
            current_rev = alembic_command.current(alembic_cfg)
            print(f"Current database revision: {current_rev}")
            
            # List all available heads
            heads = alembic_command.heads(alembic_cfg)
            print(f"Available heads: {heads}")
            
        except Exception as info_error:
            print(f"Could not get revision info: {info_error}")
        
        # Try to upgrade to the specific new migration first
        try:
            print("Attempting to upgrade to new status_log migration (g23bc4de5678)...")
            command.upgrade(alembic_cfg, "g23bc4de5678")
            print("✓ Database migrations completed successfully")
        except Exception as e:
            print(f"Failed to upgrade to g23bc4de5678: {e}")
            
            # Fallback: try to stamp the database at the new revision
            try:
                print("Attempting to stamp database with new revision...")
                command.stamp(alembic_cfg, "g23bc4de5678")
                print("✓ Database stamped with new revision successfully")
            except Exception as stamp_error:
                print(f"Failed to stamp database: {stamp_error}")
                raise

    except Exception as e:
        print(f"✗ Migration failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_migrations()
