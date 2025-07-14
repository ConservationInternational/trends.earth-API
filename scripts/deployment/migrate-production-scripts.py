#!/usr/bin/env python3
"""
Production to Staging Script Migration
Copies scripts that were created or updated within the past year from
production to staging.
"""

from datetime import datetime, timedelta, timezone
import logging
import os
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class ScriptMigration:
    def __init__(self):
        # Check for required environment variables
        required_vars = ["PROD_DB_PASSWORD", "STAGING_DB_PASSWORD"]
        missing_vars = [var for var in required_vars if not os.getenv(var)]

        if missing_vars:
            logger.error(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )
            logger.error(
                "Please set all required database passwords before running this script."
            )
            sys.exit(1)

        # Production database configuration
        self.prod_db_config = {
            "host": os.getenv("PROD_DB_HOST", "localhost"),
            "port": int(os.getenv("PROD_DB_PORT", 5432)),
            "database": os.getenv("PROD_DB_NAME", "trendsearth"),
            "user": os.getenv("PROD_DB_USER", "trendsearth"),
            "password": os.getenv("PROD_DB_PASSWORD"),  # Required, no default
        }

        # Staging database configuration
        self.staging_db_config = {
            "host": os.getenv("STAGING_DB_HOST", "localhost"),
            "port": int(os.getenv("STAGING_DB_PORT", 5433)),
            "database": os.getenv("STAGING_DB_NAME", "trendsearth_staging"),
            "user": os.getenv("STAGING_DB_USER", "trendsearth_staging"),
            "password": os.getenv("STAGING_DB_PASSWORD"),  # Required, no default
        }

        # Calculate date one year ago
        self.one_year_ago = datetime.now(timezone.utc) - timedelta(days=365)

    def connect_to_database(self, config, db_name=""):
        """Connect to a database with the given configuration."""
        try:
            db_label = db_name or config["database"]
            logger.info(
                f"Connecting to {db_label} database at "
                f"{config['host']}:{config['port']}"
            )
            conn = psycopg2.connect(**config)
            return conn
        except psycopg2.Error as e:
            logger.error(f"Error connecting to {db_label} database: {e}")
            sys.exit(1)

    def get_recent_scripts_from_production(self):
        """
        Fetch scripts from production that were created or updated in the
        past year.
        """
        conn = self.connect_to_database(self.prod_db_config, "production")

        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            logger.info(
                "Fetching scripts created or updated since "
                f"{self.one_year_ago.strftime('%Y-%m-%d')}"
            )

            cursor.execute(
                """
                SELECT id, name, slug, description, created_at, updated_at,
                       user_id, status, public, cpu_reservation, cpu_limit,
                       memory_reservation, memory_limit, environment,
                       environment_version
                FROM script
                WHERE created_at >= %s OR updated_at >= %s
                ORDER BY updated_at DESC
            """,
                (self.one_year_ago, self.one_year_ago),
            )

            scripts = cursor.fetchall()
            logger.info(f"Found {len(scripts)} recent scripts in production")

            return scripts

        except psycopg2.Error as e:
            logger.error(f"Error fetching scripts from production: {e}")
            sys.exit(1)
        finally:
            cursor.close()
            conn.close()

    def get_staging_superadmin_id(self):
        """Get the ID of the test superadmin user in staging."""
        conn = self.connect_to_database(self.staging_db_config, "staging")

        try:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id FROM "user"
                WHERE role = 'SUPERADMIN' AND email LIKE '%test%'
                LIMIT 1
            """)

            result = cursor.fetchone()
            if result:
                superadmin_id = result[0]
                logger.info(f"Found staging superadmin user ID: {superadmin_id}")
                return superadmin_id
            logger.warning("No test superadmin user found in staging database")
            return None

        except psycopg2.Error as e:
            logger.error(f"Error finding superadmin user: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def import_scripts_to_staging(self, scripts, superadmin_id):
        """Import scripts to staging database."""
        if not scripts:
            logger.info("No scripts to import")
            return

        if not superadmin_id:
            logger.error("No superadmin ID available for script ownership")
            sys.exit(1)

        conn = self.connect_to_database(self.staging_db_config, "staging")

        try:
            cursor = conn.cursor()
            imported_count = 0
            updated_count = 0

            for script in scripts:
                # Check if script already exists in staging
                cursor.execute("SELECT id FROM script WHERE id = %s", (script["id"],))
                existing = cursor.fetchone()

                if existing:
                    # Update existing script
                    cursor.execute(
                        """
                        UPDATE script SET
                            name = %s,
                            slug = %s,
                            description = %s,
                            updated_at = %s,
                            user_id = %s,
                            status = %s,
                            public = %s,
                            cpu_reservation = %s,
                            cpu_limit = %s,
                            memory_reservation = %s,
                            memory_limit = %s,
                            environment = %s,
                            environment_version = %s
                        WHERE id = %s
                    """,
                        (
                            script["name"],
                            script["slug"],
                            script["description"],
                            datetime.now(timezone.utc),
                            superadmin_id,  # Assign to test superadmin
                            script["status"],
                            script["public"],
                            script["cpu_reservation"],
                            script["cpu_limit"],
                            script["memory_reservation"],
                            script["memory_limit"],
                            script["environment"],
                            script["environment_version"],
                            script["id"],
                        ),
                    )
                    updated_count += 1
                    logger.debug(f"Updated script: {script['name']}")
                else:
                    # Insert new script
                    try:
                        cursor.execute(
                            """
                            INSERT INTO script (
                                id, name, slug, description, created_at,
                                updated_at, user_id, status, public,
                                cpu_reservation, cpu_limit, memory_reservation,
                                memory_limit, environment, environment_version
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                      %s, %s, %s, %s, %s)
                        """,
                            (
                                script["id"],
                                script["name"],
                                script["slug"],
                                script["description"],
                                script["created_at"],
                                datetime.now(timezone.utc),
                                superadmin_id,  # Assign to test superadmin
                                script["status"],
                                script["public"],
                                script["cpu_reservation"],
                                script["cpu_limit"],
                                script["memory_reservation"],
                                script["memory_limit"],
                                script["environment"],
                                script["environment_version"],
                            ),
                        )
                        imported_count += 1
                        logger.debug(f"Imported script: {script['name']}")
                    except psycopg2.IntegrityError as e:
                        if "slug" in str(e):
                            # Handle slug conflicts by appending timestamp
                            new_slug = (
                                f"{script['slug']}-{int(datetime.now().timestamp())}"
                            )
                            logger.warning(
                                f"Slug conflict for {script['slug']}, using {new_slug}"
                            )

                            cursor.execute(
                                """
                                INSERT INTO script (
                                    id, name, slug, description, created_at,
                                    updated_at, user_id, status, public,
                                    cpu_reservation, cpu_limit, memory_reservation,
                                    memory_limit, environment, environment_version
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                          %s, %s, %s, %s, %s)
                            """,
                                (
                                    script["id"],
                                    script["name"],
                                    new_slug,
                                    script["description"],
                                    script["created_at"],
                                    datetime.now(timezone.utc),
                                    superadmin_id,
                                    script["status"],
                                    script["public"],
                                    script["cpu_reservation"],
                                    script["cpu_limit"],
                                    script["memory_reservation"],
                                    script["memory_limit"],
                                    script["environment"],
                                    script["environment_version"],
                                ),
                            )
                            imported_count += 1
                        else:
                            logger.error(
                                f"Error importing script {script['name']}: {e}"
                            )
                            continue

            conn.commit()

            logger.info("✓ Script import completed:")
            logger.info(f"  New scripts imported: {imported_count}")
            logger.info(f"  Existing scripts updated: {updated_count}")
            logger.info(f"  Total scripts processed: {len(scripts)}")

        except psycopg2.Error as e:
            logger.error(f"Error importing scripts to staging: {e}")
            conn.rollback()
            sys.exit(1)
        finally:
            cursor.close()
            conn.close()

    def verify_migration(self):
        """Verify the script migration."""
        conn = self.connect_to_database(self.staging_db_config, "staging")

        try:
            cursor = conn.cursor()

            # Count total scripts
            cursor.execute("SELECT COUNT(*) FROM script")
            total_scripts = cursor.fetchone()[0]

            # Count scripts owned by test superadmin
            cursor.execute("""
                SELECT COUNT(*) FROM script s
                JOIN "user" u ON s.user_id = u.id
                WHERE u.role = 'SUPERADMIN' AND u.email LIKE '%test%'
            """)
            superadmin_scripts = cursor.fetchone()[0]

            # Count recent scripts (last year)
            cursor.execute(
                """
                SELECT COUNT(*) FROM script
                WHERE created_at >= %s OR updated_at >= %s
            """,
                (self.one_year_ago, self.one_year_ago),
            )
            recent_scripts = cursor.fetchone()[0]

            logger.info("=" * 50)
            logger.info("MIGRATION VERIFICATION")
            logger.info("=" * 50)
            logger.info(f"Total scripts in staging: {total_scripts}")
            logger.info(f"Scripts owned by test superadmin: {superadmin_scripts}")
            logger.info(f"Recent scripts (last year): {recent_scripts}")

        except psycopg2.Error as e:
            logger.error(f"Error during verification: {e}")
        finally:
            cursor.close()
            conn.close()


def main():
    """Main execution function."""
    logger.info("Starting script migration from production to staging")

    migration = ScriptMigration()

    # Get recent scripts from production
    scripts = migration.get_recent_scripts_from_production()

    # Get staging superadmin ID
    superadmin_id = migration.get_staging_superadmin_id()

    # Import scripts to staging
    migration.import_scripts_to_staging(scripts, superadmin_id)

    # Verify migration
    migration.verify_migration()

    logger.info("✓ Script migration completed successfully!")


if __name__ == "__main__":
    main()
