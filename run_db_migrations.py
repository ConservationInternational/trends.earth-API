#!/usr/bin/env python3
"""
Database migration script
"""

import sys
import logging
import atexit

# Add the project root to Python path
sys.path.insert(0, "/opt/gef-api")

# Set up logging with more verbose output
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
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
                result = db.session.execute(text("SELECT version_num FROM alembic_version")).fetchone()
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
                raise RuntimeError(f"Cannot connect to database: {db_error}")
            
            logger.info("Starting Flask-Migrate upgrade...")
            print("About to call upgrade()...")
            
            # Run the migrations with detailed logging
            upgrade()
            
            logger.info("Flask-Migrate upgrade completed successfully")
            print("✓ Database migrations completed successfully")
            
            # Check final migration state
            try:
                result = db.session.execute(text("SELECT version_num FROM alembic_version")).fetchone()
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
