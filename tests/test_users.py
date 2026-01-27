import json

from conftest import STRONG_GENERIC_PASSWORD
import pytest


@pytest.mark.usefixtures("client", "auth_headers_admin", "admin_user", "regular_user")
class TestUserFilterSort:
    def test_filter_by_role(self, client, auth_headers_admin):
        response = client.get(
            "/api/v1/user?filter=role=USER", headers=auth_headers_admin
        )
        assert response.status_code == 200
        data = response.json["data"]
        for user in data:
            assert user["role"] == "USER"

    def test_filter_by_country_like(self, client, auth_headers_admin):
        response = client.get(
            "/api/v1/user?filter=country like 'Test%'", headers=auth_headers_admin
        )
        assert response.status_code == 200
        data = response.json["data"]
        for user in data:
            assert user["country"].startswith("Test")

    def test_sort_by_name_desc(self, client, auth_headers_admin):
        response = client.get("/api/v1/user?sort=name desc", headers=auth_headers_admin)
        assert response.status_code == 200
        data = response.json["data"]
        names = [u["name"] for u in data]
        # Use case-insensitive sorting to match PostgreSQL default collation
        assert names == sorted(names, key=str.lower, reverse=True)

    def test_sort_by_created_at_asc(self, client, auth_headers_admin):
        response = client.get(
            "/api/v1/user?sort=created_at asc", headers=auth_headers_admin
        )
        assert response.status_code == 200
        data = response.json["data"]
        created_ats = [u["created_at"] for u in data]
        assert created_ats == sorted(created_ats)

    def test_pagination(self, client, auth_headers_admin):
        response = client.get(
            "/api/v1/user?page=1&per_page=1", headers=auth_headers_admin
        )
        assert response.status_code == 200
        assert "page" in response.json
        assert "per_page" in response.json
        assert "total" in response.json
        assert response.json["page"] == 1
        assert response.json["per_page"] == 1
        assert response.json["total"] >= 1
        assert len(response.json["data"]) == 1


@pytest.mark.usefixtures(
    "client", "auth_headers_superadmin", "superadmin_user", "admin_user", "regular_user"
)
class TestSuperAdminUserTests:
    """Test SUPERADMIN role functionality in existing user endpoints"""

    def test_superadmin_can_filter_by_role(self, client, auth_headers_superadmin):
        """Test that SUPERADMIN can filter users by all roles"""
        # Test filtering by USER role
        response = client.get(
            "/api/v1/user?filter=role=USER", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        data = response.json["data"]
        for user in data:
            assert user["role"] == "USER"

        # Test filtering by ADMIN role
        response = client.get(
            "/api/v1/user?filter=role=ADMIN", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        data = response.json["data"]
        for user in data:
            assert user["role"] == "ADMIN"

        # Test filtering by SUPERADMIN role
        response = client.get(
            "/api/v1/user?filter=role=SUPERADMIN", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        data = response.json["data"]
        for user in data:
            assert user["role"] == "SUPERADMIN"

    def test_superadmin_can_sort_users(self, client, auth_headers_superadmin):
        """Test that SUPERADMIN can sort users by various fields"""
        response = client.get(
            "/api/v1/user?sort=role desc", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        data = response.json["data"]
        roles = [u["role"] for u in data]
        assert roles == sorted(roles, reverse=True)

    def test_superadmin_user_creation_with_all_roles(
        self, client, auth_headers_superadmin
    ):
        """Test that SUPERADMIN can create users with any role"""
        import uuid

        roles_to_test = ["USER", "ADMIN", "SUPERADMIN"]
        created_users = []

        for role in roles_to_test:
            unique_id = str(uuid.uuid4())[:8]
            user_data = {
                "email": f"test-{role.lower()}-{unique_id}@example.com",
                "password": STRONG_GENERIC_PASSWORD,
                "name": f"Test {role} User",
                "country": "Test Country",
                "institution": "Test Institution",
                "role": role,
            }

            response = client.post(
                "/api/v1/user",
                headers=auth_headers_superadmin,
                data=json.dumps(user_data),
                content_type="application/json",
            )
            assert response.status_code == 200
            assert response.json["data"]["role"] == role
            created_users.append(response.json["data"]["id"])

        # Clean up created users
        for user_id in created_users:
            client.delete(f"/api/v1/user/{user_id}", headers=auth_headers_superadmin)

    def test_admin_cannot_create_privileged_users(self, client, auth_headers_admin):
        """Test that ADMIN cannot create ADMIN or SUPERADMIN users"""
        import uuid

        privileged_roles = ["ADMIN", "SUPERADMIN"]

        for role in privileged_roles:
            unique_id = str(uuid.uuid4())[:8]
            user_data = {
                "email": f"test-blocked-{role.lower()}-{unique_id}@example.com",
                "password": STRONG_GENERIC_PASSWORD,
                "name": f"Test {role} User",
                "country": "Test Country",
                "institution": "Test Institution",
                "role": role,
            }

            response = client.post(
                "/api/v1/user",
                headers=auth_headers_admin,
                data=json.dumps(user_data),
                content_type="application/json",
            )
            assert response.status_code == 403
            assert "Forbidden" in response.json["detail"]

    def test_regular_user_cannot_create_privileged_users(
        self, client, auth_headers_user, reset_rate_limits
    ):
        """Test that regular USER cannot create ADMIN or SUPERADMIN users"""
        # Reset rate limits to ensure clean state
        reset_rate_limits()
        import uuid

        privileged_roles = ["ADMIN", "SUPERADMIN"]

        for role in privileged_roles:
            unique_id = str(uuid.uuid4())[:8]
            user_data = {
                "email": f"test-user-blocked-{role.lower()}-{unique_id}@example.com",
                "password": STRONG_GENERIC_PASSWORD,
                "name": f"Test {role} User",
                "country": "Test Country",
                "institution": "Test Institution",
                "role": role,
            }

            response = client.post(
                "/api/v1/user",
                headers=auth_headers_user,
                data=json.dumps(user_data),
                content_type="application/json",
            )
            assert response.status_code == 403
            assert "Forbidden" in response.json["detail"]

    def test_superadmin_pagination_with_role_filter(
        self, client, auth_headers_superadmin
    ):
        """Test pagination works with role filtering for SUPERADMIN"""
        response = client.get(
            "/api/v1/user?filter=role=USER&page=1&per_page=2",
            headers=auth_headers_superadmin,
        )
        assert response.status_code == 200
        assert "page" in response.json
        assert "per_page" in response.json
        assert "total" in response.json
        assert response.json["page"] == 1
        assert response.json["per_page"] == 2

        # Verify all returned users have USER role
        for user in response.json["data"]:
            assert user["role"] == "USER"

    def test_include_exclude_functionality_for_superadmin(
        self, client, auth_headers_superadmin
    ):
        """Test include/exclude functionality works for SUPERADMIN"""
        # Test excluding fields
        response = client.get(
            "/api/v1/user?exclude=country,institution", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        data = response.json["data"]
        if data:  # If there are users
            assert "country" not in data[0]
            assert "institution" not in data[0]
            assert "email" in data[0]  # Should still have other fields

        # Test including scripts (if supported)
        response = client.get(
            "/api/v1/user?include=scripts", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        # This should work without error for SUPERADMIN
