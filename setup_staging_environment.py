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

from datetime import UTC, datetime, timedelta
import logging
import os
import sys
from urllib.parse import urlparse
import uuid

import psycopg2
from werkzeug.security import generate_password_hash

# Configure logging only if not already configured
# This prevents duplicate log messages when imported from run_db_migrations.py
logger = logging.getLogger("setup_staging_environment")
# Prevent propagation to root logger to avoid duplicate messages
logger.propagate = False
# Only add handler if none exists for this logger
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


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

        # Production database configuration (from PRODUCTION_DATABASE_URL)
        prod_database_url = os.getenv("PRODUCTION_DATABASE_URL")
        if prod_database_url:
            prod_parsed = urlparse(prod_database_url)
            self.prod_db_config = {
                "host": prod_parsed.hostname,
                "port": prod_parsed.port or 5432,
                "database": prod_parsed.path.lstrip("/"),
                "user": prod_parsed.username,
                "password": prod_parsed.password,
            }
        else:
            self.prod_db_config = None

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

    def refresh_collation_version(self):
        """Refresh the database collation version to suppress mismatch warnings.

        This is needed when the database was created on a system with a different
        glibc version than the current system. The warning is harmless but noisy.
        """
        logger.info("Checking and refreshing database collation version...")

        conn = self.connect_to_database(self.staging_db_config)
        if not conn:
            logger.warning(
                "Could not connect to staging database for collation refresh"
            )
            return

        try:
            # Need autocommit for ALTER DATABASE
            conn.autocommit = True
            cursor = conn.cursor()

            db_name = self.staging_db_config["database"]
            cursor.execute(
                f'ALTER DATABASE "{db_name}" REFRESH COLLATION VERSION'
            )
            logger.info(f"✓ Refreshed collation version for database {db_name}")

        except psycopg2.Error as e:
            # This is not critical - just a warning suppression
            logger.warning(f"Could not refresh collation version (non-critical): {e}")
        finally:
            conn.close()

    def create_test_users(self):
        """Create test users with properly hashed passwords."""
        logger.info("=" * 60)
        logger.info("CREATING TEST USERS IN STAGING DATABASE")
        logger.info("=" * 60)

        conn = self.connect_to_database(self.staging_db_config)
        if not conn:
            logger.error("❌ CRITICAL: Could not connect to staging database")
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
                        datetime.now(UTC),
                        datetime.now(UTC),
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
            logger.info("=" * 60)
            logger.info("TEST USER CREATION RESULTS")
            logger.info("=" * 60)
            logger.info(
                f"✅ Successfully created/updated {len(self.test_users)} test users"
            )
            for user_data in self.test_users:
                logger.info(f"   - {user_data['role']}: {user_data['email']}")
            logger.info("=" * 60)
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
        """Copy recent scripts from production database preserving GUIDs.

        Scripts use GUID primary keys, not integer sequences. This method:
        1. Queries production for scripts updated/created in the past year
        2. Inserts them into staging with their original GUIDs preserved
        3. Uses ON CONFLICT to update existing scripts
        4. Returns ID mapping for use in script log import

        Note: No sequence manipulation is needed since scripts use UUIDs.
        """
        logger.info("=" * 60)
        logger.info("STARTING SCRIPT IMPORT FROM PRODUCTION")
        logger.info("=" * 60)

        # Check for production database configuration
        logger.info("Checking production database credentials...")
        if not self.prod_db_config:
            logger.warning(
                "No PRODUCTION_DATABASE_URL provided - "
                "scripts will NOT be imported from production"
            )
            logger.info(
                "To enable production data import, set PRODUCTION_DATABASE_URL "
                "environment variable in the deployment workflow"
            )
            return {}
        logger.info("✅ Production database credentials are configured")

        # Log connection attempt
        logger.info(
            f"Attempting to connect to production database: "
            f"host={self.prod_db_config['host']}, "
            f"port={self.prod_db_config['port']}, "
            f"database={self.prod_db_config['database']}, "
            f"user={self.prod_db_config['user']}"
        )

        prod_conn = self.connect_to_database(self.prod_db_config)
        if not prod_conn:
            logger.error(
                "❌ CRITICAL: Could not connect to production database! "
                "Scripts will NOT be imported."
            )
            logger.error(
                f"Connection config: "
                f"host={self.prod_db_config['host']}, "
                f"port={self.prod_db_config['port']}, "
                f"database={self.prod_db_config['database']}, "
                f"user={self.prod_db_config['user']} (password: [HIDDEN])"
            )
            logger.error(
                "Possible issues: "
                "1) Network connectivity to production database, "
                "2) Incorrect credentials, "
                "3) Database not accessible from this server"
            )
            return {}

        logger.info("✅ Successfully connected to production database!")

        logger.info("Connecting to staging database...")
        staging_conn = self.connect_to_database(self.staging_db_config)
        if not staging_conn:
            logger.error(
                "❌ CRITICAL: Could not connect to staging database! "
                "Cannot import scripts."
            )
            prod_conn.close()
            return {}
        logger.info("✅ Successfully connected to staging database!")

        id_mapping = {}  # Maps old script IDs to new ones
        prod_cursor = None
        staging_cursor = None

        try:
            # Calculate date one year ago
            one_year_ago = datetime.now(UTC) - timedelta(days=365)
            logger.info(f"Filtering for scripts updated/created since: {one_year_ago}")

            prod_cursor = prod_conn.cursor()
            staging_cursor = staging_conn.cursor()

            # Enable autocommit for this connection to avoid transaction isolation
            # issues. This is needed because sequence operations may conflict with
            # transaction isolation. MUST be set BEFORE acquiring the advisory lock.
            staging_conn.autocommit = True

            # Acquire advisory lock to prevent concurrent imports
            # Use a unique ID for staging script import operations
            staging_import_lock_id = 12345
            logger.info("Acquiring advisory lock for staging import...")
            staging_cursor.execute(
                "SELECT pg_advisory_lock(%s)", (staging_import_lock_id,)
            )

            # Note: We don't delete existing scripts - we use ON CONFLICT to update them
            # Scripts use GUID primary keys, not sequences, so no sequence reset needed
            logger.info("Preparing to import/update scripts (using ON CONFLICT)...")

            # Get recent scripts from production (including ID to preserve GUIDs)
            logger.info("Executing query to fetch scripts from production database...")
            logger.info(
                f"Query: SELECT scripts WHERE created_at >= {one_year_ago} "
                f"OR updated_at >= {one_year_ago}"
            )

            prod_cursor.execute(
                """
                SELECT id, name, slug, description, created_at, updated_at, status,
                       public, cpu_reservation, cpu_limit, memory_reservation,
                       memory_limit, environment, environment_version,
                       COALESCE(restricted, false) as restricted,
                       allowed_roles, allowed_users, build_error
                FROM script
                WHERE created_at >= %s OR updated_at >= %s
                ORDER BY created_at ASC
            """,
                (one_year_ago, one_year_ago),
            )

            logger.info("Fetching query results...")
            scripts = prod_cursor.fetchall()
            logger.info(f"✅ Query completed. Found {len(scripts)} scripts to import.")

            if len(scripts) == 0:
                logger.error("=" * 60)
                logger.error("❌ CRITICAL: NO SCRIPTS FOUND IN PRODUCTION!")
                logger.error("=" * 60)
                logger.error(
                    f"The query returned 0 scripts from production database with "
                    f"created_at >= {one_year_ago} OR updated_at >= {one_year_ago}"
                )
                logger.error("Possible reasons:")
                logger.error("  1) All scripts in production are older than 1 year")
                logger.error("  2) The 'script' table is empty in production")
                logger.error("  3) There's a timezone mismatch in the date comparison")
                logger.error("  4) Connected to wrong database")
                logger.error("")
                logger.error("Please verify:")
                logger.error(
                    f"  - Production database: {self.prod_db_config['database']}"
                )
                logger.error(f"  - Production host: {self.prod_db_config['host']}")
                logger.error(f"  - Filter date: {one_year_ago}")
                logger.error("=" * 60)
            else:
                logger.info("Sample of scripts to import (first 5):")
                for i, script in enumerate(scripts[:5]):
                    logger.info(
                        f"  {i + 1}. {script[2]} (slug) - "
                        f"created: {script[4]}, updated: {script[5]}"
                    )

            imported_count = 0
            updated_count = 0
            for script in scripts:
                try:
                    script_id = script[0]  # Keep original GUID from production

                    # Insert script with GUID, using ON CONFLICT to update existing
                    staging_cursor.execute(
                        """
                        INSERT INTO script (id, name, slug, description, created_at,
                                          updated_at, user_id, status, public,
                                          cpu_reservation, cpu_limit,
                                          memory_reservation, memory_limit,
                                          environment, environment_version,
                                          restricted, allowed_roles, allowed_users,
                                          build_error)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (slug) DO UPDATE SET
                            id = EXCLUDED.id,
                            name = EXCLUDED.name,
                            description = EXCLUDED.description,
                            updated_at = EXCLUDED.updated_at,
                            user_id = EXCLUDED.user_id,
                            status = EXCLUDED.status,
                            public = EXCLUDED.public,
                            cpu_reservation = EXCLUDED.cpu_reservation,
                            cpu_limit = EXCLUDED.cpu_limit,
                            memory_reservation = EXCLUDED.memory_reservation,
                            memory_limit = EXCLUDED.memory_limit,
                            environment = EXCLUDED.environment,
                            environment_version = EXCLUDED.environment_version,
                            restricted = EXCLUDED.restricted,
                            allowed_roles = EXCLUDED.allowed_roles,
                            allowed_users = EXCLUDED.allowed_users,
                            build_error = EXCLUDED.build_error
                        RETURNING id, (xmax = 0) AS inserted
                    """,
                        (
                            script_id,  # id - keep original GUID
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
                            script[14],  # restricted
                            script[15],  # allowed_roles
                            script[16],  # allowed_users
                            script[17],  # build_error
                        ),
                    )

                    result = staging_cursor.fetchone()
                    returned_id = result[0]
                    was_inserted = result[1]

                    # Map old ID to new ID (should be the same for GUIDs)
                    id_mapping[script_id] = returned_id

                    if was_inserted:
                        imported_count += 1
                    else:
                        updated_count += 1

                    if (imported_count + updated_count) % 50 == 0:
                        logger.info(
                            f"Processed {imported_count + updated_count} scripts "
                            f"({imported_count} new, {updated_count} updated)..."
                        )

                except psycopg2.Error as e:
                    logger.warning(
                        f"Failed to import script {script[0]} with slug "
                        f"'{script[2]}': {e}"
                    )
                    # If it's an integrity error, log more details
                    if (
                        "unique constraint" in str(e).lower()
                        or "duplicate key" in str(e).lower()
                    ):
                        logger.error(
                            f"Duplicate script slug detected: {script[2]} - "
                            f"this indicates a race condition"
                        )
                        # Continue with other scripts rather than failing completely
                        continue

            # Scripts use GUIDs, not sequences, so no sequence manipulation needed
            logger.info("=" * 60)
            logger.info("SCRIPT IMPORT RESULTS")
            logger.info("=" * 60)
            logger.info(f"✅ New scripts imported: {imported_count}")
            logger.info(f"✅ Existing scripts updated: {updated_count}")
            logger.info(f"✅ Total scripts processed: {imported_count + updated_count}")
            logger.info("=" * 60)

            # No explicit commit needed since autocommit is enabled
            logger.info(
                f"Successfully imported/updated "
                f"{imported_count + updated_count} scripts "
                f"with preserved GUIDs from production"
            )
            logger.info(f"Script ID mapping created for {len(id_mapping)} scripts")

            return id_mapping

        except psycopg2.Error as e:
            logger.error(f"Error copying scripts: {e}")
            # No rollback needed since autocommit is enabled
            return {}
        finally:
            if staging_cursor:
                # Release advisory lock
                try:
                    staging_cursor.execute("SELECT pg_advisory_unlock(%s)", (12345,))
                    logger.info("Released advisory lock for staging import")
                except psycopg2.Error:
                    pass  # Lock might already be released
            if prod_cursor:
                prod_cursor.close()
            if staging_cursor:
                staging_cursor.close()
            prod_conn.close()
            staging_conn.close()

    def copy_recent_status_logs(self):
        """Copy recent status logs from production database with reassigned IDs."""
        if not self.prod_db_config:
            logger.debug(
                "No PRODUCTION_DATABASE_URL provided, skipping status logs import"
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

        prod_cursor = None
        staging_cursor = None

        try:
            prod_cursor = prod_conn.cursor()
            staging_cursor = staging_conn.cursor()

            # Enable autocommit to avoid transaction isolation issues with
            # sequence operations. MUST be set BEFORE acquiring the advisory lock.
            staging_conn.autocommit = True

            # Acquire advisory lock to prevent concurrent status log insertions
            # Use a unique ID for staging status log import operations
            staging_status_import_lock_id = 54321
            logger.info("Acquiring advisory lock for staging status log import...")
            staging_cursor.execute(
                "SELECT pg_advisory_lock(%s)", (staging_status_import_lock_id,)
            )

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

            # Reset the sequence to start from a high value to avoid conflicts
            # Use a large starting value to prevent conflicts with any existing data
            staging_cursor.execute("SELECT setval('status_log_id_seq', 100000, false)")
            logger.info(
                "Reset status_log sequence to start from 100000 to avoid conflicts"
            )

            # Calculate date one month ago
            one_month_ago = datetime.now(UTC) - timedelta(days=30)

            # Get recent status logs from production
            # Note: Production may have different column names, so we query
            # dynamically based on what exists
            prod_cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'status_log'
                ORDER BY ordinal_position
            """
            )
            prod_columns = [row[0] for row in prod_cursor.fetchall()]
            logger.info(f"Production status_log columns: {prod_columns}")

            # Skip status log import if schemas don't match - not critical
            # The status_log table may have different schemas between prod and staging
            staging_cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'status_log'
                ORDER BY ordinal_position
            """
            )
            staging_columns = [row[0] for row in staging_cursor.fetchall()]
            logger.info(f"Staging status_log columns: {staging_columns}")

            # Find common columns (excluding 'id' which we'll auto-generate)
            common_columns = [
                col for col in staging_columns
                if col in prod_columns and col != 'id'
            ]
            logger.info(f"Common columns for import: {common_columns}")

            if not common_columns or 'timestamp' not in common_columns:
                logger.warning(
                    "No compatible columns found between production and staging "
                    "status_log tables. Skipping status log import."
                )
                return

            # Build dynamic query with common columns
            columns_str = ', '.join(common_columns)

            # Note: columns_str is derived from information_schema, not user input
            # so this is safe from SQL injection
            prod_cursor.execute(
                f"""
                SELECT {columns_str}
                FROM status_log
                WHERE timestamp >= %s
                ORDER BY timestamp ASC
            """,  # noqa: S608
                (one_month_ago,),
            )

            status_logs = prod_cursor.fetchall()
            logger.info(f"Found {len(status_logs)} recent status logs to import")

            if not status_logs:
                logger.info("No status logs to import")
            else:
                # Use execute_values for fast bulk insert
                from psycopg2.extras import execute_values

                batch_size = 20000
                imported_count = 0
                for i in range(0, len(status_logs), batch_size):
                    batch = status_logs[i : i + batch_size]
                    try:
                        # Note: columns_str is derived from information_schema,
                        # not user input, so this is safe from SQL injection
                        execute_values(
                            staging_cursor,
                            f"INSERT INTO status_log ({columns_str}) "  # noqa: S608
                            "VALUES %s",
                            batch,
                            page_size=batch_size,
                        )
                        imported_count += len(batch)
                        logger.info(
                            f"Imported {imported_count}/{len(status_logs)} "
                            f"status logs..."
                        )
                    except psycopg2.Error as e:
                        logger.warning(f"Failed to import batch at offset {i}: {e}")
                        # If it's an integrity error, log more details
                        if (
                            "unique constraint" in str(e).lower()
                            or "duplicate key" in str(e).lower()
                        ):
                            logger.error(
                                "Duplicate status log key detected - "
                                "this indicates a race condition"
                            )

            # Set sequence to start from a safe value after the imported logs
            staging_cursor.execute("SELECT MAX(id) FROM status_log")
            max_id = staging_cursor.fetchone()[0]
            if max_id:
                # Set sequence to start from max_id + 10000 to provide large buffer
                # This prevents conflicts with concurrent status monitoring tasks
                next_val = max_id + 10000
                staging_cursor.execute(
                    f"SELECT setval('status_log_id_seq', {next_val}, false)"
                )
                logger.info(
                    f"Reset status_log sequence to start from {next_val} "
                    f"to avoid future conflicts (buffer: 10000)"
                )
            else:
                # If no logs were imported, ensure sequence starts from a safe
                # high value
                staging_cursor.execute(
                    "SELECT setval('status_log_id_seq', 100000, false)"
                )
                logger.info("No status logs imported, sequence remains at 100000")

            # No explicit commit needed since autocommit is enabled
            logger.info(
                f"Successfully imported {imported_count} status logs "
                f"with new sequential IDs"
            )

        except psycopg2.Error as e:
            logger.error(f"Error copying status logs: {e}")
            # No rollback needed since autocommit is enabled
        finally:
            if staging_cursor:
                # Release advisory lock
                try:
                    staging_cursor.execute("SELECT pg_advisory_unlock(%s)", (54321,))
                    logger.info("Released advisory lock for staging status log import")
                except psycopg2.Error:
                    pass  # Lock might already be released
            if prod_cursor:
                prod_cursor.close()
            if staging_cursor:
                staging_cursor.close()
            prod_conn.close()
            staging_conn.close()

    def copy_script_logs(self, script_id_mapping):
        """Copy script logs for imported scripts using ID mapping."""
        if not self.prod_db_config:
            logger.debug(
                "No PRODUCTION_DATABASE_URL provided, skipping script logs import"
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

        # Enable autocommit to avoid transaction isolation issues
        staging_conn.autocommit = True

        prod_cursor = None
        staging_cursor = None

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

            # Build batch of logs with remapped script IDs
            logs_to_insert = []
            for log in script_logs:
                old_script_id = log[3]
                new_script_id = script_id_mapping.get(old_script_id)
                if new_script_id:
                    # (text, register_date, script_id)
                    logs_to_insert.append((log[1], log[2], new_script_id))

            if not logs_to_insert:
                logger.info("No script logs to import after ID mapping")
                return

            # Use execute_values for fast bulk insert
            from psycopg2.extras import execute_values

            batch_size = 20000
            imported_count = 0
            for i in range(0, len(logs_to_insert), batch_size):
                batch = logs_to_insert[i : i + batch_size]
                try:
                    execute_values(
                        staging_cursor,
                        "INSERT INTO script_log (text, register_date, script_id) "
                        "VALUES %s",
                        batch,
                        page_size=batch_size,
                    )
                    imported_count += len(batch)
                    logger.info(
                        f"Imported {imported_count}/{len(logs_to_insert)} "
                        f"script logs..."
                    )
                except psycopg2.Error as e:
                    logger.warning(f"Failed to import batch at offset {i}: {e}")

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

            # No explicit commit needed since autocommit is enabled
            logger.info(
                f"Successfully imported {imported_count} script logs "
                f"with remapped script IDs"
            )

        except psycopg2.Error as e:
            logger.error(f"Error copying script logs: {e}")
            # No rollback needed since autocommit is enabled
        finally:
            if prod_cursor:
                prod_cursor.close()
            if staging_cursor:
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
            one_year_ago = datetime.now(UTC) - timedelta(days=365)
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

            # Display test user credentials (passwords hidden for security)
            logger.info("\nTest User Accounts Created:")
            for user_data in self.test_users:
                logger.info(
                    f"  {user_data['role']}: {user_data['email']} "
                    f"(password: [CONFIGURED])"
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
            # Refresh collation version to suppress mismatch warnings
            self.refresh_collation_version()

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
