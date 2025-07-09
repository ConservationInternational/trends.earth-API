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
        from flask_migrate import upgrade

        from gefapi import app

        print("Creating Flask app context...")
        with app.app_context():
            print("App context created, starting migration upgrade...")
            # Run the migrations
            upgrade()
            print("✓ Database migrations completed successfully")

    except Exception as e:
        print(f"✗ Migration failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_migrations()
