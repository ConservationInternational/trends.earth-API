#!/usr/bin/env python3
"""
Quick diagnostic script to verify production database connection and script count.
This can be run manually to troubleshoot script import issues.

Usage:
    python scripts/verify_production_connection.py

Environment variables required:
    PROD_DB_HOST
    PROD_DB_PORT
    PROD_DB_NAME
    PROD_DB_USER
    PROD_DB_PASSWORD
"""

from datetime import UTC, datetime, timedelta
import os
import sys

try:
    import psycopg2
except ImportError:
    print("❌ Error: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


def main():
    """Test production database connection and query for recent scripts."""
    print("=" * 70)
    print("PRODUCTION DATABASE CONNECTION DIAGNOSTIC")
    print("=" * 70)
    print()

    # Get environment variables
    required_vars = [
        "PROD_DB_HOST",
        "PROD_DB_PORT",
        "PROD_DB_NAME",
        "PROD_DB_USER",
        "PROD_DB_PASSWORD",
    ]

    print("Checking environment variables...")
    missing_vars = []
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            missing_vars.append(var)
            print(f"  ❌ {var}: NOT SET")
        else:
            # Show value but hide password
            if "PASSWORD" in var:
                print(f"  ✅ {var}: {'*' * len(value)} (hidden)")
            else:
                print(f"  ✅ {var}: {value}")

    if missing_vars:
        print()
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("Please set these variables and try again.")
        sys.exit(1)

    print()
    print("Attempting to connect to production database...")
    print(f"  Host: {os.getenv('PROD_DB_HOST')}:{os.getenv('PROD_DB_PORT')}")
    print(f"  Database: {os.getenv('PROD_DB_NAME')}")
    print(f"  User: {os.getenv('PROD_DB_USER')}")

    try:
        conn = psycopg2.connect(
            host=os.getenv("PROD_DB_HOST"),
            port=int(os.getenv("PROD_DB_PORT", 5432)),
            database=os.getenv("PROD_DB_NAME"),
            user=os.getenv("PROD_DB_USER"),
            password=os.getenv("PROD_DB_PASSWORD"),
        )
        print("✅ Successfully connected to production database!")
    except psycopg2.Error as e:
        print(f"❌ Failed to connect to production database: {e}")
        sys.exit(1)

    print()
    print("Querying for scripts...")

    try:
        cursor = conn.cursor()

        # Count all scripts
        cursor.execute("SELECT COUNT(*) FROM script")
        total_scripts = cursor.fetchone()[0]
        print(f"  Total scripts in database: {total_scripts}")

        # Count recent scripts (last year)
        one_year_ago = datetime.now(UTC) - timedelta(days=365)
        print(f"  Filtering for scripts updated/created since: {one_year_ago}")

        cursor.execute(
            """
            SELECT COUNT(*) FROM script
            WHERE created_at >= %s OR updated_at >= %s
        """,
            (one_year_ago, one_year_ago),
        )
        recent_scripts = cursor.fetchone()[0]
        print(f"  Scripts from last year: {recent_scripts}")

        if recent_scripts > 0:
            print()
            print("Sample of recent scripts (first 5):")
            cursor.execute(
                """
                SELECT id, name, slug, created_at, updated_at
                FROM script
                WHERE created_at >= %s OR updated_at >= %s
                ORDER BY updated_at DESC
                LIMIT 5
            """,
                (one_year_ago, one_year_ago),
            )
            for i, row in enumerate(cursor.fetchall(), 1):
                print(f"  {i}. {row[2]} - created: {row[3]}, updated: {row[4]}")
        else:
            print()
            print("❌ WARNING: No scripts found from the last year!")
            print("   This explains why staging import is returning 0 scripts.")
            print()
            print("   Checking oldest script in database...")
            cursor.execute(
                """
                SELECT id, name, slug, created_at, updated_at
                FROM script
                ORDER BY updated_at DESC
                LIMIT 1
            """
            )
            row = cursor.fetchone()
            if row:
                print("   Most recently updated script:")
                print(f"     - Name: {row[1]}")
                print(f"     - Slug: {row[2]}")
                print(f"     - Created: {row[3]}")
                print(f"     - Updated: {row[4]}")
                print()
                print(
                    "   Consider adjusting the time filter or checking "
                    "if production data is being updated."
                )

        cursor.close()
        conn.close()

        print()
        print("=" * 70)
        print("DIAGNOSTIC COMPLETE")
        print("=" * 70)

    except psycopg2.Error as e:
        print(f"❌ Error querying database: {e}")
        conn.close()
        sys.exit(1)


if __name__ == "__main__":
    main()
