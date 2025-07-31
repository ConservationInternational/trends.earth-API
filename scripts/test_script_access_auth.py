"""
Test script access control authorization

This script tests that only ADMIN and SUPERADMIN users can modify script access controls.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gefapi import app
from gefapi.models.script import Script
from gefapi.utils.script_access import can_manage_script_access


def test_script_access_authorization():
    """Test that only admin users can manage script access"""

    with app.app_context():
        # Get a test script
        script = Script.query.first()
        if not script:
            print("❌ No scripts found to test with")
            return False

        print(f"Testing access control authorization for script: {script.slug}")
        print("=" * 60)

        # Test cases for different user roles
        test_cases = [
            ("USER", False),
            ("ADMIN", True),
            ("SUPERADMIN", True),
            (None, False),  # No role/anonymous
        ]

        for role, should_have_access in test_cases:
            # Create a mock user object
            class MockUser:
                def __init__(self, role, user_id="test-user-123"):
                    self.role = role
                    self.id = user_id
                    self.email = (
                        f"test-{role.lower() if role else 'anonymous'}@example.com"
                    )

            user = MockUser(role) if role else None

            # Test access control
            has_access = can_manage_script_access(user, script)

            role_display = role if role else "Anonymous"
            status = "✅ PASS" if has_access == should_have_access else "❌ FAIL"
            access_display = "CAN" if has_access else "CANNOT"
            expected_display = "SHOULD" if should_have_access else "SHOULD NOT"

            print(
                f"{status} {role_display:12} {access_display:6} manage access ({expected_display} be able to)"
            )

        print("\n✅ Authorization test completed!")
        return True


if __name__ == "__main__":
    test_script_access_authorization()
