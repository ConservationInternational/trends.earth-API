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
        command.upgrade(alembic_cfg, "head")
        print("✓ Database migrations completed successfully")

    except Exception as e:
        print(f"✗ Migration failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_migrations()
