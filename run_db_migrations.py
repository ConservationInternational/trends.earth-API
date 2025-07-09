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
        # Import Flask and create a minimal app for migrations only
        from flask import Flask
        from flask_migrate import Migrate, upgrade
        from flask_sqlalchemy import SQLAlchemy

        from gefapi.config import SETTINGS

        print("Creating minimal Flask app for migrations...")

        # Create a minimal Flask app just for migrations
        app = Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = SETTINGS.get("SQLALCHEMY_DATABASE_URI")
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

        # Initialize database and migration objects
        db = SQLAlchemy(app)
        migrate = Migrate(app, db)

        print("App created, running migrations...")
        with app.app_context():
            print("App context created, running upgrade...")
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
