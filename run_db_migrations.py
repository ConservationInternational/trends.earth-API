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
        import os
        from alembic.config import Config
        from alembic import command
        from gefapi.config import SETTINGS

        print("Setting up Alembic configuration...")
        
        # Set up Alembic configuration
        alembic_cfg = Config("/opt/gef-api/migrations/alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", SETTINGS.get("SQLALCHEMY_DATABASE_URI"))
        
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
