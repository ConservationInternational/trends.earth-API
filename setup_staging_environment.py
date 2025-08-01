#!/usr/bin/env python3
"""
Comprehensive Staging Environment Setup Script
This script runs inside the Docker container after migrations and sets up:
1. Test users with proper password hashing
2. Recent scripts from production database
3. Recent status logs from production
4. Script logs for imported scripts

Designed to run inside the migrate service where all dependencies are available.
"""

from datetime import datetime, timedelta, timezone
import logging
import os
import sys
from urllib.parse import urlparse
import uuid

import psycopg2
from werkzeug.security import generate_password_hash

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class StagingEnvironmentSetup:
    def __init__(self):
        # Only run in staging environment
        if os.getenv("ENVIRONMENT") != "staging":
            logger.info("Not in staging environment, skipping setup")
            sys.exit(0)

        logger.info("Starting staging environment setup...")

        # Get database connection from DATABASE_URL (container environment)
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            logger.error("DATABASE_URL environment variable is required")
            sys.exit(1)

        # Parse DATABASE_URL for staging database
        parsed = urlparse(database_url)
        self.staging_db_config = {
            "host": parsed.hostname,
            "port": parsed.port or 5432,
            "database": parsed.path.lstrip("/"),
            "user": parsed.username,
            "password": parsed.password,
        }

        # Production database configuration
        self.prod_db_config = {
            "host": os.getenv("PROD_DB_HOST", "localhost"),
            "port": int(os.getenv("PROD_DB_PORT", 5432)),
            "database": os.getenv("PROD_DB_NAME", "trendsearth"),
            "user": os.getenv("PROD_DB_USER", "trendsearth"),
            "password": os.getenv("PROD_DB_PASSWORD"),
        }

        # Test users configuration
        self.test_users = [
            {
                "email": os.getenv(
                    "TEST_SUPERADMIN_EMAIL", "test-superadmin@example.com"
                ),
                "password": os.getenv("TEST_SUPERADMIN_PASSWORD"),
                "name": "Test Superadmin User",
                "role": "SUPERADMIN",
            },
            {
                "email": os.getenv("TEST_ADMIN_EMAIL", "test-admin@example.com"),
                "password": os.getenv("TEST_ADMIN_PASSWORD"),
                "name": "Test Admin User",
                "role": "ADMIN",
            },
            {
                "email": os.getenv("TEST_USER_EMAIL", "test-user@example.com"),
                "password": os.getenv("TEST_USER_PASSWORD"),
                "name": "Test Regular User",
                "role": "USER",
            },
        ]

        # Check for required test user passwords
        required_vars = [
            "TEST_SUPERADMIN_PASSWORD",
            "TEST_ADMIN_PASSWORD",
            "TEST_USER_PASSWORD",
        ]
        missing_vars = [var for var in required_vars if not os.getenv(var)]

        if missing_vars:
            logger.error(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )
            sys.exit(1)

    def connect_to_database(self, db_config):
        """Connect to a database."""
        try:
            logger.info(
                f"Connecting to database {db_config['database']} at "
                f"{db_config['host']}:{db_config['port']}"
            )
            conn = psycopg2.connect(**db_config)
            return conn
        except psycopg2.Error as e:
            logger.error(f"Error connecting to database: {e}")
            return None

    def create_test_users(self):
        """Create test users with properly hashed passwords."""
        logger.info("Creating test users in staging database...")

        conn = self.connect_to_database(self.staging_db_config)
        if not conn:
            logger.error("Could not connect to staging database")
            return None

        superadmin_id = None
        cursor = None

        try:
            cursor = conn.cursor()

            for user_data in self.test_users:
                user_id = str(uuid.uuid4())
                hashed_password = generate_password_hash(user_data["password"])

                logger.info(
                    f"Creating user: {user_data['email']} with role: "
                    f"{user_data['role']}"
                )

                # Insert or update user
                cursor.execute(
                    """
                    INSERT INTO "user" (id, email, name, country, institution,
                                       password, role, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (email) DO UPDATE SET
                        name = EXCLUDED.name,
                        password = EXCLUDED.password,
                        role = EXCLUDED.role,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id;
                """,
                    (
                        user_id,
                        user_data["email"],
                        user_data["name"],
                        "Test Country",
                        "Test Institution",
                        hashed_password,
                        user_data["role"],
                        datetime.now(timezone.utc),
                        datetime.now(timezone.utc),
                    ),
                )

                result = cursor.fetchone()
                actual_user_id = result[0] if result else user_id

                if user_data["role"] == "SUPERADMIN":
                    superadmin_id = actual_user_id

                logger.info(
                    f"✓ User {user_data['email']} created/updated with ID: "
                    f"{actual_user_id}"
                )

            conn.commit()
            logger.info("Test users created successfully")
            return superadmin_id

        except psycopg2.Error as e:
            logger.error(f"Error creating users: {e}")
            conn.rollback()
            return None
        finally:
            if cursor:
                cursor.close()
            conn.close()

    def copy_recent_scripts(self, superadmin_id):
        """Copy recent scripts from production database with reassigned IDs."""
        if not self.prod_db_config["password"]:
            logger.warning(
                "No production database password provided, skipping script import"
            )
            return {}

        logger.info("Copying recent scripts from production database...")

        prod_conn = self.connect_to_database(self.prod_db_config)
        if not prod_conn:
            logger.warning(
                "Could not connect to production database, skipping script import"
            )
            return {}

        staging_conn = self.connect_to_database(self.staging_db_config)
        if not staging_conn:
            logger.error("Could not connect to staging database")
            prod_conn.close()
            return {}

        id_mapping = {}  # Maps old script IDs to new ones
        prod_cursor = None
        staging_cursor = None

        try:
            # Calculate date one year ago
            one_year_ago = datetime.now() - timedelta(days=365)

            prod_cursor = prod_conn.cursor()
            staging_cursor = staging_conn.cursor()

            # Clear existing scripts to avoid conflicts
            logger.info("Clearing existing scripts from staging...")
            staging_cursor.execute("DELETE FROM script")

            # Reset the script ID sequence
            staging_cursor.execute("SELECT setval('script_id_seq', 1, false)")

            # Get recent scripts from production (excluding ID for reassignment)
            prod_cursor.execute(
                """
                SELECT id, name, slug, description, created_at, updated_at, status,
                       public, cpu_reservation, cpu_limit, memory_reservation,
                       memory_limit, environment, environment_version
                FROM script
                WHERE created_at >= %s OR updated_at >= %s
                ORDER BY created_at ASC
            """,
                (one_year_ago, one_year_ago),
            )

            scripts = prod_cursor.fetchall()
            logger.info(f"Found {len(scripts)} recent scripts to import")

            imported_count = 0
            for script in scripts:
                try:
                    old_script_id = script[0]

                    # Insert script without ID to get new sequential ID
                    staging_cursor.execute(
                        """
                        INSERT INTO script (name, slug, description, created_at,
                                          updated_at, user_id, status, public,
                                          cpu_reservation, cpu_limit,
                                          memory_reservation, memory_limit,
                                          environment, environment_version)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s)
                        RETURNING id
                    """,
                        (
                            script[1],  # name
                            script[2],  # slug
                            script[3],  # description
                            script[4],  # created_at
                            script[5],  # updated_at
                            superadmin_id,
                            script[6],  # status
                            script[7],  # public
                            script[8],  # cpu_reservation
                            script[9],  # cpu_limit
                            script[10],  # memory_reservation
                            script[11],  # memory_limit
                            script[12],  # environment
                            script[13],  # environment_version
                        ),
                    )

                    new_script_id = staging_cursor.fetchone()[0]
                    id_mapping[old_script_id] = new_script_id
                    imported_count += 1

                    if imported_count % 50 == 0:
                        logger.info(f"Imported {imported_count} scripts so far...")

                except psycopg2.Error as e:
                    logger.warning(f"Failed to import script {script[0]}: {e}")

            # Set sequence to start from a safe value after the imported scripts
            staging_cursor.execute("SELECT MAX(id) FROM script")
            max_id = staging_cursor.fetchone()[0]
            if max_id:
                # Set sequence to start from max_id + 1000 to provide buffer
                next_val = max_id + 1000
                staging_cursor.execute(
                    f"SELECT setval('script_id_seq', {next_val}, false)"
                )
                logger.info(
                    f"Reset script sequence to start from {next_val} "
                    f"to avoid future conflicts"
                )

            staging_conn.commit()
            logger.info(
                f"Successfully imported {imported_count} scripts "
                f"with new sequential IDs"
            )
            logger.info(f"Script ID mapping created for {len(id_mapping)} scripts")

            return id_mapping

        except psycopg2.Error as e:
            logger.error(f"Error copying scripts: {e}")
            staging_conn.rollback()
            return {}
        finally:
            if prod_cursor:
                prod_cursor.close()
            if staging_cursor:
                staging_cursor.close()
            prod_conn.close()
            staging_conn.close()

    def copy_recent_status_logs(self):
        """Copy recent status logs from production database with reassigned IDs."""
        if not self.prod_db_config["password"]:
            logger.warning(
                "No production database password provided, skipping status logs import"
            )
            return

        logger.info("Copying recent status logs from production database...")

        prod_conn = self.connect_to_database(self.prod_db_config)
        if not prod_conn:
            logger.warning(
                "Could not connect to production database, skipping status logs import"
            )
            return

        staging_conn = self.connect_to_database(self.staging_db_config)
        if not staging_conn:
            logger.error("Could not connect to staging database")
            prod_conn.close()
            return

        try:
            prod_cursor = prod_conn.cursor()
            staging_cursor = staging_conn.cursor()

            # Check if status_log table exists in both databases
            prod_cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'status_log'
                )
            """
            )
            prod_has_table = prod_cursor.fetchone()[0]

            staging_cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'status_log'
                )
            """
            )
            staging_has_table = staging_cursor.fetchone()[0]

            if not prod_has_table:
                logger.warning("Production database does not have status_log table")
                return

            if not staging_has_table:
                logger.warning("Staging database does not have status_log table")
                return

            # Clear existing status logs to avoid conflicts
            logger.info("Clearing existing status logs from staging...")
            staging_cursor.execute("DELETE FROM status_log")

            # Reset the sequence to start from 1
            staging_cursor.execute("ALTER SEQUENCE status_log_id_seq RESTART WITH 1")

            # Calculate date one month ago
            one_month_ago = datetime.now() - timedelta(days=30)

            # Get recent status logs from production (excluding ID for reassignment)
            prod_cursor.execute(
                """
                SELECT timestamp, executions_active, executions_ready,
                       executions_running, executions_finished, executions_failed,
                       executions_count, users_count, scripts_count
                FROM status_log
                WHERE timestamp >= %s
                ORDER BY timestamp ASC
            """,
                (one_month_ago,),
            )

            status_logs = prod_cursor.fetchall()
            logger.info(f"Found {len(status_logs)} recent status logs to import")

            imported_count = 0
            for log in status_logs:
                try:
                    # Insert without ID to let the sequence generate new IDs
                    staging_cursor.execute(
                        """
                        INSERT INTO status_log (timestamp, executions_active,
                                              executions_ready, executions_running,
                                              executions_finished, executions_failed,
                                              executions_count, users_count,
                                              scripts_count)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                        log,
                    )
                    imported_count += 1
                except psycopg2.Error as e:
                    logger.warning(f"Failed to import status log: {e}")

            # Update the sequence to start from a safe value after the imported logs
            staging_cursor.execute("SELECT MAX(id) FROM status_log")
            max_id = staging_cursor.fetchone()[0]
            if max_id:
                # Set sequence to start from max_id + 1000 to provide buffer
                next_val = max_id + 1000
                staging_cursor.execute(
                    f"ALTER SEQUENCE status_log_id_seq RESTART WITH {next_val}"
                )
                logger.info(
                    f"Reset sequence to start from {next_val} to avoid future conflicts"
                )

            staging_conn.commit()
            logger.info(
                f"Successfully imported {imported_count} status logs "
                f"with new sequential IDs"
            )

        except psycopg2.Error as e:
            logger.error(f"Error copying status logs: {e}")
            staging_conn.rollback()
        finally:
            if prod_cursor:
                prod_cursor.close()
            if staging_cursor:
                staging_cursor.close()
            prod_conn.close()
            staging_conn.close()

    def copy_script_logs(self, script_id_mapping):
        """Copy script logs for imported scripts using ID mapping."""
        if not self.prod_db_config["password"]:
            logger.warning(
                "No production database password provided, skipping script logs import"
            )
            return

        if not script_id_mapping:
            logger.warning(
                "No script ID mapping available, skipping script logs import"
            )
            return

        logger.info(
            "Copying script logs for imported scripts from production database..."
        )

        prod_conn = self.connect_to_database(self.prod_db_config)
        if not prod_conn:
            logger.warning(
                "Could not connect to production database, skipping script logs import"
            )
            return

        staging_conn = self.connect_to_database(self.staging_db_config)
        if not staging_conn:
            logger.error("Could not connect to staging database")
            prod_conn.close()
            return

        try:
            prod_cursor = prod_conn.cursor()
            staging_cursor = staging_conn.cursor()

            # Check if script_log table exists in both databases
            prod_cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'script_log'
                )
            """
            )
            prod_has_table = prod_cursor.fetchone()[0]

            staging_cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'script_log'
                )
            """
            )
            staging_has_table = staging_cursor.fetchone()[0]

            if not prod_has_table:
                logger.warning("Production database does not have script_log table")
                return

            if not staging_has_table:
                logger.warning("Staging database does not have script_log table")
                return

            # Clear existing script logs
            logger.info("Clearing existing script logs from staging...")
            staging_cursor.execute("DELETE FROM script_log")

            # Reset the script_log ID sequence if it exists
            from contextlib import suppress

            with suppress(psycopg2.Error):
                staging_cursor.execute("SELECT setval('script_log_id_seq', 1, false)")

            # Get script logs for the old script IDs that were imported
            old_script_ids = list(script_id_mapping.keys())
            if not old_script_ids:
                logger.info("No script IDs to import logs for")
                return

            # Convert to tuple for SQL IN clause
            old_script_ids_tuple = tuple(old_script_ids)

            # Get script logs for scripts that were imported
            if len(old_script_ids_tuple) == 1:
                # Handle single item tuple
                prod_cursor.execute(
                    """
                    SELECT id, text, register_date, script_id
                    FROM script_log
                    WHERE script_id = %s
                    ORDER BY register_date ASC
                """,
                    (old_script_ids_tuple[0],),
                )
            else:
                prod_cursor.execute(
                    """
                    SELECT id, text, register_date, script_id
                    FROM script_log
                    WHERE script_id IN %s
                    ORDER BY register_date ASC
                """,
                    (old_script_ids_tuple,),
                )

            script_logs = prod_cursor.fetchall()
            logger.info(f"Found {len(script_logs)} script logs to import")

            imported_count = 0
            for log in script_logs:
                try:
                    old_script_id = log[3]
                    new_script_id = script_id_mapping.get(old_script_id)

                    if new_script_id:
                        # Insert without ID to let sequence generate new IDs
                        staging_cursor.execute(
                            """
                            INSERT INTO script_log (text, register_date, script_id)
                            VALUES (%s, %s, %s)
                        """,
                            (log[1], log[2], new_script_id),
                        )
                        imported_count += 1

                        if imported_count % 100 == 0:
                            logger.info(
                                f"Imported {imported_count} script logs so far..."
                            )

                except psycopg2.Error as e:
                    logger.warning(f"Failed to import script log {log[0]}: {e}")

            # Set sequence to start from a safe value after the imported logs
            try:
                staging_cursor.execute("SELECT MAX(id) FROM script_log")
                max_id = staging_cursor.fetchone()[0]
                if max_id:
                    # Set sequence to start from max_id + 1000 to provide buffer
                    next_val = max_id + 1000
                    staging_cursor.execute(
                        f"SELECT setval('script_log_id_seq', {next_val}, false)"
                    )
                    logger.info(f"Reset script_log sequence to start from {next_val}")
            except psycopg2.Error:
                # Sequence might not exist, that's okay
                pass

            staging_conn.commit()
            logger.info(
                f"Successfully imported {imported_count} script logs "
                f"with remapped script IDs"
            )

        except psycopg2.Error as e:
            logger.error(f"Error copying script logs: {e}")
            staging_conn.rollback()
        finally:
            prod_cursor.close()
            staging_cursor.close()
            prod_conn.close()
            staging_conn.close()

    def verify_setup(self):
        """Verify the staging environment setup."""
        logger.info("Verifying staging database setup...")

        conn = self.connect_to_database(self.staging_db_config)
        if not conn:
            logger.error("Could not connect to staging database for verification")
            return

        cursor = None
        try:
            cursor = conn.cursor()

            # Count scripts
            cursor.execute("SELECT COUNT(*) FROM script")
            script_count = cursor.fetchone()[0]

            # Count users by role
            cursor.execute(
                'SELECT role, COUNT(*) FROM "user" GROUP BY role ORDER BY role'
            )
            user_roles = cursor.fetchall()

            # Count recent scripts (last year)
            one_year_ago = datetime.now() - timedelta(days=365)
            cursor.execute(
                """
                SELECT COUNT(*) FROM script
                WHERE created_at >= %s OR updated_at >= %s
            """,
                (one_year_ago, one_year_ago),
            )
            recent_scripts = cursor.fetchone()[0]

            logger.info("=" * 50)
            logger.info("STAGING ENVIRONMENT VERIFICATION RESULTS")
            logger.info("=" * 50)
            logger.info(f"Total Scripts: {script_count}")
            logger.info(f"Recent Scripts (last year): {recent_scripts}")
            logger.info("Users by Role:")
            for role, count in user_roles:
                logger.info(f"  {role}: {count}")

            # Display test user credentials
            logger.info("\nTest User Credentials:")
            for user_data in self.test_users:
                logger.info(
                    f"  {user_data['role']}: {user_data['email']} "
                    f"(password: {user_data['password']})"
                )

        except psycopg2.Error as e:
            logger.error(f"Error during verification: {e}")
        finally:
            if cursor:
                cursor.close()
            conn.close()

    def run(self):
        """Run the complete staging environment setup."""
        try:
            # Create test users first
            superadmin_id = self.create_test_users()
            if not superadmin_id:
                logger.error("Failed to create test users")
                return False

            # Import production data and get script ID mapping
            script_id_mapping = self.copy_recent_scripts(superadmin_id)
            self.copy_recent_status_logs()
            self.copy_script_logs(script_id_mapping)

            # Verify setup
            self.verify_setup()

            logger.info("✓ Staging environment setup completed successfully!")
            return True

        except Exception as e:
            logger.error(f"Failed to setup staging environment: {e}")
            import traceback

            traceback.print_exc()
            return False


def main():
    """Main execution function."""
    logger.info("Starting staging environment setup")

    setup = StagingEnvironmentSetup()
    success = setup.run()

    if success:
        print("✓ Staging environment setup completed successfully!")
        sys.exit(0)
    else:
        print("✗ Staging environment setup failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
