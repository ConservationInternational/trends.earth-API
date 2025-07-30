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
            cursor.close()
            conn.close()

    def copy_recent_scripts(self, superadmin_id):
        """Copy recent scripts from production database."""
        if not self.prod_db_config["password"]:
            logger.warning(
                "No production database password provided, skipping script import"
            )
            return

        logger.info("Copying recent scripts from production database...")

        prod_conn = self.connect_to_database(self.prod_db_config)
        if not prod_conn:
            logger.warning(
                "Could not connect to production database, skipping script import"
            )
            return

        staging_conn = self.connect_to_database(self.staging_db_config)
        if not staging_conn:
            logger.error("Could not connect to staging database")
            prod_conn.close()
            return

        try:
            # Calculate date one year ago
            one_year_ago = datetime.now() - timedelta(days=365)

            prod_cursor = prod_conn.cursor()
            staging_cursor = staging_conn.cursor()

            # Get recent scripts from production
            prod_cursor.execute(
                """
                SELECT id, name, slug, description, created_at, updated_at, status,
                       public, cpu_reservation, cpu_limit, memory_reservation,
                       memory_limit, environment, environment_version
                FROM script
                WHERE created_at >= %s OR updated_at >= %s
            """,
                (one_year_ago, one_year_ago),
            )

            scripts = prod_cursor.fetchall()
            logger.info(f"Found {len(scripts)} recent scripts to import")

            imported_count = 0
            for script in scripts:
                try:
                    # Insert script with superadmin ownership
                    staging_cursor.execute(
                        """
                        INSERT INTO script (id, name, slug, description, created_at,
                                          updated_at, user_id, status, public,
                                          cpu_reservation, cpu_limit,
                                          memory_reservation, memory_limit,
                                          environment, environment_version)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s)
                        ON CONFLICT (id) DO NOTHING
                    """,
                        (
                            script[0],
                            script[1],
                            script[2],
                            script[3],
                            script[4],
                            script[5],
                            superadmin_id,
                            script[6],
                            script[7],
                            script[8],
                            script[9],
                            script[10],
                            script[11],
                            script[12],
                            script[13],
                        ),
                    )
                    if staging_cursor.rowcount > 0:
                        imported_count += 1
                except psycopg2.Error as e:
                    logger.warning(f"Failed to import script {script[0]}: {e}")

            staging_conn.commit()
            logger.info(f"Successfully imported {imported_count} scripts")

        except psycopg2.Error as e:
            logger.error(f"Error copying scripts: {e}")
            staging_conn.rollback()
        finally:
            prod_cursor.close()
            staging_cursor.close()
            prod_conn.close()
            staging_conn.close()

    def copy_recent_status_logs(self):
        """Copy recent status logs from production database."""
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

            # Calculate date one month ago
            one_month_ago = datetime.now() - timedelta(days=30)

            # Get recent status logs from production
            prod_cursor.execute(
                """
                SELECT id, timestamp, executions_active, executions_ready,
                       executions_running, executions_finished, executions_failed,
                       executions_count, users_count, scripts_count
                FROM status_log
                WHERE timestamp >= %s
                ORDER BY timestamp DESC
            """,
                (one_month_ago,),
            )

            status_logs = prod_cursor.fetchall()
            logger.info(f"Found {len(status_logs)} recent status logs to import")

            imported_count = 0
            for log in status_logs:
                try:
                    staging_cursor.execute(
                        """
                        INSERT INTO status_log (id, timestamp, executions_active,
                                              executions_ready, executions_running,
                                              executions_finished, executions_failed,
                                              executions_count, users_count,
                                              scripts_count)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                    """,
                        log,
                    )
                    if staging_cursor.rowcount > 0:
                        imported_count += 1
                except psycopg2.Error as e:
                    logger.warning(f"Failed to import status log {log[0]}: {e}")

            staging_conn.commit()
            logger.info(f"Successfully imported {imported_count} status logs")

        except psycopg2.Error as e:
            logger.error(f"Error copying status logs: {e}")
            staging_conn.rollback()
        finally:
            prod_cursor.close()
            staging_cursor.close()
            prod_conn.close()
            staging_conn.close()

    def copy_script_logs(self):
        """Copy script logs for imported scripts from production database."""
        if not self.prod_db_config["password"]:
            logger.warning(
                "No production database password provided, skipping script logs import"
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

            # Calculate date one year ago
            one_year_ago = datetime.now() - timedelta(days=365)

            # Get script logs for scripts that exist in staging
            prod_cursor.execute(
                """
                SELECT sl.id, sl.text, sl.register_date, sl.script_id
                FROM script_log sl
                INNER JOIN script s ON sl.script_id = s.id
                WHERE s.created_at >= %s OR s.updated_at >= %s
                ORDER BY sl.register_date DESC
            """,
                (one_year_ago, one_year_ago),
            )

            script_logs = prod_cursor.fetchall()
            logger.info(f"Found {len(script_logs)} script logs to import")

            imported_count = 0
            for log in script_logs:
                try:
                    # Check if the script exists in staging first
                    staging_cursor.execute(
                        "SELECT 1 FROM script WHERE id = %s", (log[3],)
                    )
                    if staging_cursor.fetchone():
                        staging_cursor.execute(
                            """
                            INSERT INTO script_log (id, text, register_date, script_id)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                        """,
                            log,
                        )
                        if staging_cursor.rowcount > 0:
                            imported_count += 1
                except psycopg2.Error as e:
                    logger.warning(f"Failed to import script log {log[0]}: {e}")

            staging_conn.commit()
            logger.info(f"Successfully imported {imported_count} script logs")

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

            # Import production data
            self.copy_recent_scripts(superadmin_id)
            self.copy_recent_status_logs()
            self.copy_script_logs()

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
