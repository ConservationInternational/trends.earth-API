#!/usr/bin/env python3
"""
Staging Database User Setup Script
Creates test users with properly hashed passwords for the staging environment.
"""

from datetime import datetime, timezone
import logging
import os
import sys
import uuid

import psycopg2
from werkzeug.security import generate_password_hash

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class StagingUserSetup:
    def __init__(self):
        # Check for required environment variables
        required_vars = [
            "STAGING_DB_PASSWORD",
            "TEST_SUPERADMIN_PASSWORD",
            "TEST_ADMIN_PASSWORD",
            "TEST_USER_PASSWORD",
        ]
        missing_vars = [var for var in required_vars if not os.getenv(var)]

        if missing_vars:
            logger.error(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )
            logger.error(
                "Please set all required environment variables before "
                "running this script."
            )
            sys.exit(1)

        self.db_config = {
            "host": os.getenv("STAGING_DB_HOST", "localhost"),
            "port": int(os.getenv("STAGING_DB_PORT", 5433)),
            "database": os.getenv("STAGING_DB_NAME", "trendsearth_staging"),
            "user": os.getenv("STAGING_DB_USER", "trendsearth_staging"),
            "password": os.getenv("STAGING_DB_PASSWORD"),  # Required, no default
        }

        self.test_users = [
            {
                "email": os.getenv(
                    "TEST_SUPERADMIN_EMAIL", "test-superadmin@example.com"
                ),
                "password": os.getenv(
                    "TEST_SUPERADMIN_PASSWORD"
                ),  # Required, no default
                "name": "Test Superadmin User",
                "role": "SUPERADMIN",
            },
            {
                "email": os.getenv("TEST_ADMIN_EMAIL", "test-admin@example.com"),
                "password": os.getenv("TEST_ADMIN_PASSWORD"),  # Required, no default
                "name": "Test Admin User",
                "role": "ADMIN",
            },
            {
                "email": os.getenv("TEST_USER_EMAIL", "test-user@example.com"),
                "password": os.getenv("TEST_USER_PASSWORD"),  # Required, no default
                "name": "Test Regular User",
                "role": "USER",
            },
        ]

    def connect_to_database(self):
        """Connect to the staging database."""
        try:
            logger.info(
                f"Connecting to database {self.db_config['database']} at "
                f"{self.db_config['host']}:{self.db_config['port']}"
            )
            conn = psycopg2.connect(**self.db_config)
            return conn
        except psycopg2.Error as e:
            logger.error(f"Error connecting to database: {e}")
            sys.exit(1)

    def create_test_users(self):
        """Create test users with properly hashed passwords."""
        conn = self.connect_to_database()
        superadmin_id = None

        try:
            cursor = conn.cursor()

            for user_data in self.test_users:
                user_id = str(uuid.uuid4())
                hashed_password = generate_password_hash(user_data["password"])

                logger.info(
                    f"Creating user: {user_data['email']} with "
                    f"role: {user_data['role']}"
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
                    f"✓ User {user_data['email']} created/updated with "
                    f"ID: {actual_user_id}"
                )

            conn.commit()
            return superadmin_id

        except psycopg2.Error as e:
            logger.error(f"Error creating users: {e}")
            conn.rollback()
            sys.exit(1)
        finally:
            cursor.close()
            conn.close()

    def update_script_ownership(self, superadmin_id):
        """Update script ownership to the test superadmin user."""
        if not superadmin_id:
            logger.warning(
                "No superadmin ID provided, skipping script ownership update"
            )
            return

        conn = self.connect_to_database()

        try:
            cursor = conn.cursor()

            logger.info(
                f"Updating script ownership to superadmin user: {superadmin_id}"
            )

            # Update all scripts to be owned by the test superadmin
            cursor.execute(
                """
                UPDATE script
                SET user_id = %s, updated_at = %s
                WHERE user_id IS NOT NULL
            """,
                (superadmin_id, datetime.now(timezone.utc)),
            )

            updated_count = cursor.rowcount
            conn.commit()

            logger.info(
                f"✓ Updated ownership of {updated_count} scripts to test "
                f"superadmin user"
            )

        except psycopg2.Error as e:
            logger.error(f"Error updating script ownership: {e}")
            conn.rollback()
            sys.exit(1)
        finally:
            cursor.close()
            conn.close()

    def verify_setup(self):
        """Verify the database setup."""
        conn = self.connect_to_database()

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
            cursor.execute("""
                SELECT COUNT(*) FROM script
                WHERE created_at >= NOW() - INTERVAL '1 year'
                   OR updated_at >= NOW() - INTERVAL '1 year'
            """)
            recent_scripts = cursor.fetchone()[0]

            logger.info("=" * 50)
            logger.info("VERIFICATION RESULTS")
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
            sys.exit(1)
        finally:
            cursor.close()
            conn.close()


def main():
    """Main execution function."""
    logger.info("Starting staging database user setup")

    setup = StagingUserSetup()

    # Create test users and get superadmin ID
    superadmin_id = setup.create_test_users()

    # Update script ownership
    setup.update_script_ownership(superadmin_id)

    # Verify setup
    setup.verify_setup()

    logger.info("✓ Staging database user setup completed successfully!")


if __name__ == "__main__":
    main()
