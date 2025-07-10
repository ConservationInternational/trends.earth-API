"""
End-to-end integration tests for SUPERADMIN role implementation
Tests the complete flow from user creation to permission enforcement
"""

import json

import pytest

from gefapi.models import User


@pytest.mark.usefixtures("client", "app")
class TestSuperAdminIntegration:
    """End-to-end integration tests for SUPERADMIN functionality"""

    def test_complete_superadmin_workflow(
        self, client, app, auth_headers_superadmin, superadmin_user
    ):
        """Test complete workflow: create user, update role, perform admin actions, cleanup"""

        # Step 1: Create a regular user as SUPERADMIN
        user_data = {
            "email": "integration-test@example.com",
            "password": "password123",
            "name": "Integration Test User",
            "country": "Test Country",
            "institution": "Test Institution",
            "role": "USER",
        }

        create_response = client.post(
            "/api/v1/user",
            headers=auth_headers_superadmin,
            data=json.dumps(user_data),
            content_type="application/json",
        )
        assert create_response.status_code == 200
        created_user_id = create_response.json["data"]["id"]
        assert create_response.json["data"]["role"] == "USER"

        # Step 2: Upgrade user to ADMIN role
        update_data = {"role": "ADMIN"}
        update_response = client.patch(
            f"/api/v1/user/{created_user_id}",
            headers=auth_headers_superadmin,
            data=json.dumps(update_data),
            content_type="application/json",
        )
        assert update_response.status_code == 200
        assert update_response.json["data"]["role"] == "ADMIN"

        # Step 3: Update user profile information
        profile_data = {
            "name": "Updated Integration User",
            "country": "Updated Country",
            "institution": "Updated Institution",
        }
        profile_response = client.patch(
            f"/api/v1/user/{created_user_id}",
            headers=auth_headers_superadmin,
            data=json.dumps(profile_data),
            content_type="application/json",
        )
        assert profile_response.status_code == 200
        assert profile_response.json["data"]["name"] == "Updated Integration User"
        assert profile_response.json["data"]["country"] == "Updated Country"

        # Step 4: Change user password
        password_data = {"new_password": "newpassword456"}
        password_response = client.patch(
            f"/api/v1/user/{created_user_id}/change-password",
            headers=auth_headers_superadmin,
            data=json.dumps(password_data),
            content_type="application/json",
        )
        assert password_response.status_code == 200

        # Step 5: Verify user can be retrieved
        get_response = client.get(
            f"/api/v1/user/{created_user_id}", headers=auth_headers_superadmin
        )
        assert get_response.status_code == 200
        assert get_response.json["data"]["role"] == "ADMIN"
        assert get_response.json["data"]["name"] == "Updated Integration User"

        # Step 6: Clean up - delete the user
        delete_response = client.delete(
            f"/api/v1/user/{created_user_id}", headers=auth_headers_superadmin
        )
        assert delete_response.status_code == 200

        # Step 7: Verify user is deleted
        verify_response = client.get(
            f"/api/v1/user/{created_user_id}", headers=auth_headers_superadmin
        )
        assert verify_response.status_code == 404

    def test_permission_hierarchy_enforcement(
        self, client, auth_headers_superadmin, auth_headers_admin, auth_headers_user
    ):
        """Test that permission hierarchy is properly enforced across all endpoints"""

        # Create a test user to work with
        user_data = {
            "email": "hierarchy-test@example.com",
            "password": "password123",
            "name": "Hierarchy Test User",
            "country": "Test Country",
            "institution": "Test Institution",
            "role": "USER",
        }

        create_response = client.post(
            "/api/v1/user",
            headers=auth_headers_superadmin,
            data=json.dumps(user_data),
            content_type="application/json",
        )
        assert create_response.status_code == 200
        test_user_id = create_response.json["data"]["id"]

        try:
            # Test user listing access
            superadmin_list = client.get(
                "/api/v1/user", headers=auth_headers_superadmin
            )
            admin_list = client.get("/api/v1/user", headers=auth_headers_admin)
            user_list = client.get("/api/v1/user", headers=auth_headers_user)

            assert superadmin_list.status_code == 200
            assert admin_list.status_code == 200
            assert user_list.status_code == 403

            # Test user profile updates (only SUPERADMIN should succeed)
            update_data = {"name": "Updated by test"}

            superadmin_update = client.patch(
                f"/api/v1/user/{test_user_id}",
                headers=auth_headers_superadmin,
                data=json.dumps(update_data),
                content_type="application/json",
            )
            admin_update = client.patch(
                f"/api/v1/user/{test_user_id}",
                headers=auth_headers_admin,
                data=json.dumps(update_data),
                content_type="application/json",
            )
            user_update = client.patch(
                f"/api/v1/user/{test_user_id}",
                headers=auth_headers_user,
                data=json.dumps(update_data),
                content_type="application/json",
            )

            assert superadmin_update.status_code == 200
            assert admin_update.status_code == 403
            assert user_update.status_code == 403

            # Test password changes (only SUPERADMIN should succeed)
            password_data = {"new_password": "testpassword"}

            superadmin_password = client.patch(
                f"/api/v1/user/{test_user_id}/change-password",
                headers=auth_headers_superadmin,
                data=json.dumps(password_data),
                content_type="application/json",
            )
            admin_password = client.patch(
                f"/api/v1/user/{test_user_id}/change-password",
                headers=auth_headers_admin,
                data=json.dumps(password_data),
                content_type="application/json",
            )
            user_password = client.patch(
                f"/api/v1/user/{test_user_id}/change-password",
                headers=auth_headers_user,
                data=json.dumps(password_data),
                content_type="application/json",
            )

            assert superadmin_password.status_code == 200
            assert admin_password.status_code == 403
            assert user_password.status_code == 403

            # Test role changes (only SUPERADMIN should succeed)
            role_data = {"role": "ADMIN"}

            superadmin_role = client.patch(
                f"/api/v1/user/{test_user_id}",
                headers=auth_headers_superadmin,
                data=json.dumps(role_data),
                content_type="application/json",
            )
            admin_role = client.patch(
                f"/api/v1/user/{test_user_id}",
                headers=auth_headers_admin,
                data=json.dumps(role_data),
                content_type="application/json",
            )
            user_role = client.patch(
                f"/api/v1/user/{test_user_id}",
                headers=auth_headers_user,
                data=json.dumps(role_data),
                content_type="application/json",
            )

            assert superadmin_role.status_code == 200
            assert admin_role.status_code == 403
            assert user_role.status_code == 403

            # Test status logs access (SUPERADMIN and ADMIN should succeed)
            superadmin_status = client.get(
                "/api/v1/status", headers=auth_headers_superadmin
            )
            admin_status = client.get("/api/v1/status", headers=auth_headers_admin)
            user_status = client.get("/api/v1/status", headers=auth_headers_user)

            assert superadmin_status.status_code == 200
            assert admin_status.status_code == 200
            assert user_status.status_code == 403

        finally:
            # Clean up
            client.delete(
                f"/api/v1/user/{test_user_id}", headers=auth_headers_superadmin
            )

    def test_gef_user_special_privileges(self, client, auth_headers_gef):
        """Test that gef@gef.com has superadmin privileges regardless of role"""

        # Create a test user using GEF account
        user_data = {
            "email": "gef-privilege-test@example.com",
            "password": "password123",
            "name": "GEF Privilege Test",
            "country": "Test Country",
            "institution": "Test Institution",
            "role": "ADMIN",  # GEF user should be able to create admin
        }

        create_response = client.post(
            "/api/v1/user",
            headers=auth_headers_gef,
            data=json.dumps(user_data),
            content_type="application/json",
        )
        assert create_response.status_code == 200
        test_user_id = create_response.json["data"]["id"]
        assert create_response.json["data"]["role"] == "ADMIN"

        try:
            # GEF user should be able to update roles
            role_update = {"role": "SUPERADMIN"}
            role_response = client.patch(
                f"/api/v1/user/{test_user_id}",
                headers=auth_headers_gef,
                data=json.dumps(role_update),
                content_type="application/json",
            )
            assert role_response.status_code == 200
            assert role_response.json["data"]["role"] == "SUPERADMIN"

            # GEF user should be able to change passwords
            password_data = {"new_password": "gefchangedpassword"}
            password_response = client.patch(
                f"/api/v1/user/{test_user_id}/change-password",
                headers=auth_headers_gef,
                data=json.dumps(password_data),
                content_type="application/json",
            )
            assert password_response.status_code == 200

            # GEF user should be able to update profiles
            profile_data = {"name": "Updated by GEF"}
            profile_response = client.patch(
                f"/api/v1/user/{test_user_id}",
                headers=auth_headers_gef,
                data=json.dumps(profile_data),
                content_type="application/json",
            )
            assert profile_response.status_code == 200
            assert profile_response.json["data"]["name"] == "Updated by GEF"

        finally:
            # Clean up
            client.delete(f"/api/v1/user/{test_user_id}", headers=auth_headers_gef)

    def test_role_creation_restrictions(
        self, client, auth_headers_superadmin, auth_headers_admin, auth_headers_user
    ):
        """Test role creation restrictions across different user types"""

        test_cases = [
            (
                "USER",
                "user",
                auth_headers_user,
                [("USER", 200), ("ADMIN", 403), ("SUPERADMIN", 403)],
            ),
            (
                "ADMIN",
                "admin",
                auth_headers_admin,
                [("USER", 200), ("ADMIN", 403), ("SUPERADMIN", 403)],
            ),
            (
                "SUPERADMIN",
                "superadmin",
                auth_headers_superadmin,
                [("USER", 200), ("ADMIN", 200), ("SUPERADMIN", 200)],
            ),
        ]

        created_users = []

        try:
            for (
                creator_role,
                creator_name,
                creator_headers,
                allowed_roles,
            ) in test_cases:
                for target_role, expected_status in allowed_roles:
                    user_data = {
                        "email": f"role-test-{creator_name}-creates-{target_role.lower()}@example.com",
                        "password": "password123",
                        "name": f"{creator_role} creates {target_role}",
                        "country": "Test Country",
                        "institution": "Test Institution",
                        "role": target_role,
                    }

                    response = client.post(
                        "/api/v1/user",
                        headers=creator_headers,
                        data=json.dumps(user_data),
                        content_type="application/json",
                    )

                    assert response.status_code == expected_status, (
                        f"{creator_role} should {'succeed' if expected_status == 200 else 'fail'} "
                        f"creating {target_role} user, got {response.status_code}"
                    )

                    if response.status_code == 200:
                        assert response.json["data"]["role"] == target_role
                        created_users.append(response.json["data"]["id"])

        finally:
            # Clean up all created users
            for user_id in created_users:
                client.delete(
                    f"/api/v1/user/{user_id}", headers=auth_headers_superadmin
                )

    def test_database_consistency_after_operations(
        self, client, app, auth_headers_superadmin
    ):
        """Test that database remains consistent after SUPERADMIN operations"""

        with app.app_context():
            # Get initial counts
            initial_user_count = User.query.count()
            initial_superadmin_count = User.query.filter_by(role="SUPERADMIN").count()
            initial_admin_count = User.query.filter_by(role="ADMIN").count()

            # Create a user
            user_data = {
                "email": "consistency-test@example.com",
                "password": "password123",
                "name": "Consistency Test User",
                "country": "Test Country",
                "institution": "Test Institution",
                "role": "USER",
            }

            create_response = client.post(
                "/api/v1/user",
                headers=auth_headers_superadmin,
                data=json.dumps(user_data),
                content_type="application/json",
            )
            assert create_response.status_code == 200
            user_id = create_response.json["data"]["id"]

            # Verify count increased
            new_user_count = User.query.count()
            assert new_user_count == initial_user_count + 1

            # Update user to SUPERADMIN
            update_data = {"role": "SUPERADMIN"}
            update_response = client.patch(
                f"/api/v1/user/{user_id}",
                headers=auth_headers_superadmin,
                data=json.dumps(update_data),
                content_type="application/json",
            )
            assert update_response.status_code == 200

            # Verify SUPERADMIN count increased
            new_superadmin_count = User.query.filter_by(role="SUPERADMIN").count()
            assert new_superadmin_count == initial_superadmin_count + 1

            # Update to ADMIN
            update_data = {"role": "ADMIN"}
            update_response = client.patch(
                f"/api/v1/user/{user_id}",
                headers=auth_headers_superadmin,
                data=json.dumps(update_data),
                content_type="application/json",
            )
            assert update_response.status_code == 200

            # Verify counts are correct
            final_superadmin_count = User.query.filter_by(role="SUPERADMIN").count()
            final_admin_count = User.query.filter_by(role="ADMIN").count()
            assert (
                final_superadmin_count == initial_superadmin_count
            )  # Back to original
            assert final_admin_count == initial_admin_count + 1  # Increased by 1

            # Delete user
            delete_response = client.delete(
                f"/api/v1/user/{user_id}", headers=auth_headers_superadmin
            )
            assert delete_response.status_code == 200

            # Verify counts are back to original
            final_user_count = User.query.count()
            final_admin_count_after_delete = User.query.filter_by(role="ADMIN").count()
            assert final_user_count == initial_user_count
            assert final_admin_count_after_delete == initial_admin_count
