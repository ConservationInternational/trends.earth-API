"""
Tests for staging environment setup script

Tests focus on validating the datetime timezone handling fix for script filtering.
"""

import ast
import re
from unittest.mock import Mock

from setup_staging_environment import StagingEnvironmentSetup


def test_staging_users_are_verified_on_insert_and_update():
    setup = StagingEnvironmentSetup.__new__(StagingEnvironmentSetup)
    setup.staging_db_config = {}
    setup.test_users = [
        {
            "email": f"{role.lower()}@example.com",
            "password": "test-password",
            "name": role,
            "role": role,
        }
        for role in ("SUPERADMIN", "ADMIN", "USER")
    ]
    setup.api_environment_user = {
        "email": "automation@example.com",
        "password": "test-password",
        "name": "Automation",
        "role": "USER",
    }
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.fetchone.side_effect = [(f"user-{index}",) for index in range(4)]
    setup.connect_to_database = Mock(return_value=connection)

    assert setup.create_test_users() == "user-0"

    assert cursor.execute.call_count == 4
    for query, _params in (call.args for call in cursor.execute.call_args_list):
        normalized_query = " ".join(query.split())
        assert (
            "email_verified, email_verified_at, gee_license_acknowledged"
            in normalized_query
        )
        assert "TRUE, CURRENT_TIMESTAMP, TRUE" in normalized_query
        assert "email_verified = TRUE" in normalized_query
        assert "gee_license_acknowledged = TRUE" in normalized_query
        assert (
            'COALESCE( "user".email_verified_at, EXCLUDED.email_verified_at )'
            in normalized_query
        )


class TestStagingEnvironmentSetupDatetimeHandling:
    """Test datetime timezone handling in staging environment setup"""

    def test_all_datetime_now_calls_use_utc(self):
        """Verify that all datetime.now() calls use UTC timezone in the source code"""
        # Read the setup_staging_environment.py file
        with open("setup_staging_environment.py") as f:
            source_code = f.read()

        # Parse the Python source code into an AST
        tree = ast.parse(source_code)

        # Find all Call nodes that call datetime.now()
        datetime_now_calls = []
        for node in ast.walk(tree):
            # Check if this is a call to datetime.now()
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "now"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "datetime"
            ):
                # Get the line number and check arguments
                line_num = node.lineno
                has_utc_arg = any(
                    isinstance(arg, ast.Name) and arg.id == "UTC" for arg in node.args
                )
                datetime_now_calls.append((line_num, has_utc_arg))

        # Verify we found the expected calls
        assert len(datetime_now_calls) > 0, "Should find datetime.now() calls"

        # Verify all datetime.now() calls use UTC
        calls_without_utc = [
            line_num for line_num, has_utc in datetime_now_calls if not has_utc
        ]

        assert len(calls_without_utc) == 0, (
            f"Found datetime.now() calls without UTC on lines: {calls_without_utc}"
        )

        # Verify we found the expected number of calls (should be 7 total)
        # Lines 224, 225 (create_test_users - 2 calls),
        # Lines 285, 286 (create_test_users - 2 more calls),
        # Line 394 (copy_recent_scripts),
        # Line 688 (copy_recent_status_logs),
        # Line 1179 (verify_setup)
        assert len(datetime_now_calls) == 7, (
            f"Expected 7 datetime.now() calls, found {len(datetime_now_calls)}"
        )

    def test_copy_recent_scripts_uses_utc_timezone(self):
        """Verify copy_recent_scripts method uses UTC timezone for one_year_ago calculation"""
        with open("setup_staging_environment.py") as f:
            source_code = f.read()

        # Find the line with one_year_ago calculation in copy_recent_scripts
        # Should be around line 227
        match = re.search(
            r"one_year_ago = datetime\.now\((\w+)\) - timedelta\(days=365\)",
            source_code,
        )

        assert match is not None, "Should find one_year_ago calculation"
        timezone_arg = match.group(1)
        assert timezone_arg == "UTC", f"Expected UTC timezone, got {timezone_arg}"

    def test_copy_recent_status_logs_uses_utc_timezone(self):
        """Verify copy_recent_status_logs method uses UTC timezone for one_month_ago calculation"""
        with open("setup_staging_environment.py") as f:
            source_code = f.read()

        # Find the line with one_month_ago calculation in copy_recent_status_logs
        # Should be around line 470
        match = re.search(
            r"one_month_ago = datetime\.now\((\w+)\) - timedelta\(days=30\)",
            source_code,
        )

        assert match is not None, "Should find one_month_ago calculation"
        timezone_arg = match.group(1)
        assert timezone_arg == "UTC", f"Expected UTC timezone, got {timezone_arg}"

    def test_verify_setup_uses_utc_timezone(self):
        """Verify verify_setup method uses UTC timezone for one_year_ago calculation"""
        with open("setup_staging_environment.py") as f:
            source_code = f.read()

        # Find all one_year_ago calculations (there are two: in copy_recent_scripts and verify_setup)
        matches = list(
            re.finditer(
                r"one_year_ago = datetime\.now\((\w+)\) - timedelta\(days=365\)",
                source_code,
            )
        )

        assert len(matches) == 2, (
            f"Expected 2 one_year_ago calculations, found {len(matches)}"
        )

        # Both should use UTC
        for match in matches:
            timezone_arg = match.group(1)
            assert timezone_arg == "UTC", f"Expected UTC timezone, got {timezone_arg}"

    def test_datetime_utc_import_exists(self):
        """Verify that UTC is imported from datetime module"""
        with open("setup_staging_environment.py") as f:
            source_code = f.read()

        # Check for UTC import
        assert "from datetime import UTC" in source_code, (
            "Should import UTC from datetime"
        )
