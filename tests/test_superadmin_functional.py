"""
Functional tests for SUPERADMIN role and user management operations
"""

import json

from conftest import NEW_STRONG_PASSWORD
import pytest

from gefapi.config import SETTINGS
from gefapi.models import User

ENVIRONMENT_USER_EMAIL = SETTINGS["API_ENVIRONMENT_USER"]


@pytest.mark.usefixtures(
    "client",
    "superadmin_user",
    "admin_user",
    "regular_user",
    "environment_user",
)
class TestSuperAdminUserManagement:
    """Test SUPERADMIN exclusive user management operations"""

    def test_superadmin_can_create_admin_user(
        self, client, auth_headers_superadmin, sample_user_data
    ):
        """Test that SUPERADMIN can create admin users"""
        sample_user_data["role"] = "ADMIN"
        response = client.post(
            "/api/v1/user",
            headers=auth_headers_superadmin,
            data=json.dumps(sample_user_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json["data"]["role"] == "ADMIN"

    def test_superadmin_can_create_superadmin_user(
        self, client, auth_headers_superadmin, sample_user_data
    ):
        """Test that SUPERADMIN can create other superadmin users"""
        sample_user_data["role"] = "SUPERADMIN"
        response = client.post(
            "/api/v1/user",
            headers=auth_headers_superadmin,
            data=json.dumps(sample_user_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json["data"]["role"] == "SUPERADMIN"

    def test_admin_cannot_create_admin_user(
        self, client, auth_headers_admin, sample_user_data
    ):
        """Test that ADMIN cannot create admin users"""
        sample_user_data["role"] = "ADMIN"
        response = client.post(
            "/api/v1/user",
            headers=auth_headers_admin,
            data=json.dumps(sample_user_data),
            content_type="application/json",
        )
        assert response.status_code == 403
        assert "Forbidden" in response.json["detail"]

    def test_admin_cannot_create_superadmin_user(
        self, client, auth_headers_admin, sample_user_data
    ):
        """Test that ADMIN cannot create superadmin users"""
        sample_user_data["role"] = "SUPERADMIN"
        response = client.post(
            "/api/v1/user",
            headers=auth_headers_admin,
            data=json.dumps(sample_user_data),
            content_type="application/json",
        )
        assert response.status_code == 403
        assert "Forbidden" in response.json["detail"]

    def test_user_cannot_create_admin_user(
        self, client, auth_headers_user, sample_user_data
    ):
        """Test that regular USER cannot create admin users"""
        sample_user_data["role"] = "ADMIN"
        response = client.post(
            "/api/v1/user",
            headers=auth_headers_user,
            data=json.dumps(sample_user_data),
            content_type="application/json",
        )
        assert response.status_code == 403
        assert "Forbidden" in response.json["detail"]

    def test_superadmin_can_change_user_role(
        self, client, auth_headers_superadmin, regular_user, app
    ):
        """Test that SUPERADMIN can change user roles"""
        with app.app_context():
            # Re-query user to ensure it's attached to current session
            user = User.query.filter_by(email=regular_user.email).first()
            update_data = {"role": "ADMIN"}
            response = client.patch(
                f"/api/v1/user/{user.id}",
                headers=auth_headers_superadmin,
                data=json.dumps(update_data),
                content_type="application/json",
            )
            assert response.status_code == 200
            assert response.json["data"]["role"] == "ADMIN"

    def test_admin_cannot_change_user_role(
        self, client, auth_headers_admin, regular_user, app
    ):
        """Test that ADMIN cannot change user roles"""
        with app.app_context():
            # Re-query user to ensure it's attached to current session
            user = User.query.filter_by(email=regular_user.email).first()
            update_data = {"role": "ADMIN"}
            response = client.patch(
                f"/api/v1/user/{user.id}",
                headers=auth_headers_admin,
                data=json.dumps(update_data),
                content_type="application/json",
            )
            assert response.status_code == 403
            assert "Forbidden" in response.json["detail"]

    def test_superadmin_can_update_user_profile(
        self, client, auth_headers_superadmin, regular_user, app
    ):
        """Test that SUPERADMIN can update user profiles"""
        with app.app_context():
            # Re-query user to ensure it's attached to current session
            user = User.query.filter_by(email=regular_user.email).first()
            update_data = {"name": "Updated Name", "country": "Updated Country"}
            response = client.patch(
                f"/api/v1/user/{user.id}",
                headers=auth_headers_superadmin,
                data=json.dumps(update_data),
                content_type="application/json",
            )
            assert response.status_code == 200
            assert response.json["data"]["name"] == "Updated Name"
            assert response.json["data"]["country"] == "Updated Country"

    def test_admin_cannot_update_user_profile(
        self, client, auth_headers_admin, regular_user, app
    ):
        """Test that ADMIN cannot update user profiles"""
        with app.app_context():
            # Re-query user to ensure it's attached to current session
            user = User.query.filter_by(email=regular_user.email).first()
            update_data = {"name": "Updated Name"}
            response = client.patch(
                f"/api/v1/user/{user.id}",
                headers=auth_headers_admin,
                data=json.dumps(update_data),
                content_type="application/json",
            )
            assert response.status_code == 403
            assert "Forbidden" in response.json["detail"]

    def test_superadmin_can_delete_user(
        self, client, auth_headers_superadmin, sample_user_data
    ):
        """Test that SUPERADMIN can delete users"""
        # First create a user to delete
        create_response = client.post(
            "/api/v1/user",
            headers=auth_headers_superadmin,
            data=json.dumps(sample_user_data),
            content_type="application/json",
        )
        assert create_response.status_code == 200
        user_id = create_response.json["data"]["id"]

        # Then delete the user
        delete_response = client.delete(
            f"/api/v1/user/{user_id}", headers=auth_headers_superadmin
        )
        assert delete_response.status_code == 200

    def test_admin_cannot_delete_user(
        self, client, auth_headers_admin, regular_user, app
    ):
        """Test that ADMIN cannot delete users"""
        with app.app_context():
            # Re-query user to ensure it's attached to current session
            user = User.query.filter_by(email=regular_user.email).first()
            response = client.delete(
                f"/api/v1/user/{user.id}", headers=auth_headers_admin
            )
            assert response.status_code == 403
            assert "Forbidden" in response.json["detail"]

    def test_superadmin_can_change_user_password(
        self, client, auth_headers_superadmin, regular_user, app
    ):
        """Test that SUPERADMIN can change user passwords"""
        with app.app_context():
            # Re-query user to ensure it's attached to current session
            user = User.query.filter_by(email=regular_user.email).first()
            password_data = {"new_password": NEW_STRONG_PASSWORD}
            response = client.patch(
                f"/api/v1/user/{user.id}/change-password",
                headers=auth_headers_superadmin,
                data=json.dumps(password_data),
                content_type="application/json",
            )
        assert response.status_code == 200

    def test_admin_can_change_regular_user_password(
        self, client, auth_headers_admin, regular_user, app
    ):
        """Test that ADMIN can change regular user passwords"""
        with app.app_context():
            # Re-query user to ensure it's attached to current session
            user = User.query.filter_by(email=regular_user.email).first()
            password_data = {"new_password": NEW_STRONG_PASSWORD}
            response = client.patch(
                f"/api/v1/user/{user.id}/change-password",
                headers=auth_headers_admin,
                data=json.dumps(password_data),
                content_type="application/json",
            )
            assert response.status_code == 200

    def test_admin_cannot_change_superadmin_password(
        self, client, auth_headers_admin, superadmin_user, app
    ):
        """Test that ADMIN cannot change SUPERADMIN passwords"""
        with app.app_context():
            # Re-query superadmin user to ensure it's attached to current session
            superadmin = User.query.filter_by(email=superadmin_user.email).first()
            password_data = {"new_password": NEW_STRONG_PASSWORD}
            response = client.patch(
                f"/api/v1/user/{superadmin.id}/change-password",
                headers=auth_headers_admin,
                data=json.dumps(password_data),
                content_type="application/json",
            )
            assert response.status_code == 403
            assert "cannot change superadmin" in response.json["detail"].lower()

    def test_environment_user_has_superadmin_privileges(
        self, client, auth_headers_environment_user, sample_user_data
    ):
        """Test that the automation user has superadmin privileges regardless of role"""
        # Test user creation with admin role
        sample_user_data["role"] = "ADMIN"
        response = client.post(
            "/api/v1/user",
            headers=auth_headers_environment_user,
            data=json.dumps(sample_user_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json["data"]["role"] == "ADMIN"

    def test_cannot_delete_environment_user(self, client, auth_headers_superadmin):
        """Test that the automation user cannot be deleted"""
        response = client.delete(
            f"/api/v1/user/{ENVIRONMENT_USER_EMAIL}", headers=auth_headers_superadmin
        )
        assert response.status_code == 403
        assert "Forbidden" in response.json["detail"]


@pytest.mark.usefixtures("client", "superadmin_user", "admin_user", "regular_user")
class TestAdminFeatureAccess:
    """Test that admin features are accessible to both ADMIN and SUPERADMIN"""

    def test_superadmin_can_access_user_list(self, client, auth_headers_superadmin):
        """Test that SUPERADMIN can access user list"""
        response = client.get("/api/v1/user", headers=auth_headers_superadmin)
        assert response.status_code == 200
        assert "data" in response.json

    def test_admin_can_access_user_list(self, client, auth_headers_admin):
        """Test that ADMIN can access user list"""
        response = client.get("/api/v1/user", headers=auth_headers_admin)
        assert response.status_code == 200
        assert "data" in response.json

    def test_user_cannot_access_user_list(self, client, auth_headers_user):
        """Test that regular USER cannot access user list"""
        response = client.get("/api/v1/user", headers=auth_headers_user)
        assert response.status_code == 403
        assert "Forbidden" in response.json["detail"]

    def test_superadmin_can_access_status_logs(self, client, auth_headers_superadmin):
        """Test that SUPERADMIN can access status logs"""
        response = client.get("/api/v1/status", headers=auth_headers_superadmin)
        assert response.status_code == 200

    def test_admin_can_access_status_logs(self, client, auth_headers_admin):
        """Test that ADMIN can access status logs"""
        response = client.get("/api/v1/status", headers=auth_headers_admin)
        assert response.status_code == 200

    def test_user_cannot_access_status_logs(self, client, auth_headers_user):
        """Test that regular USER cannot access status logs"""
        response = client.get("/api/v1/status", headers=auth_headers_user)
        assert response.status_code == 403
        assert "Forbidden" in response.json["detail"]

    def test_superadmin_can_access_specific_user(
        self, client, auth_headers_superadmin, regular_user, app
    ):
        """Test that SUPERADMIN can access specific user data"""
        with app.app_context():
            # Re-query user to ensure it's attached to current session
            user = User.query.filter_by(email=regular_user.email).first()
            response = client.get(
                f"/api/v1/user/{user.id}", headers=auth_headers_superadmin
            )
            assert response.status_code == 200
            assert response.json["data"]["id"] == str(user.id)

    def test_admin_can_access_specific_user(
        self, client, auth_headers_admin, regular_user, app
    ):
        """Test that ADMIN can access specific user data"""
        with app.app_context():
            # Re-query user to ensure it's attached to current session
            user = User.query.filter_by(email=regular_user.email).first()
            response = client.get(f"/api/v1/user/{user.id}", headers=auth_headers_admin)
            assert response.status_code == 200
            assert response.json["data"]["id"] == str(user.id)

    def test_user_cannot_access_other_user_data(
        self, client, auth_headers_user, admin_user, app
    ):
        """Test that regular USER cannot access other user data"""
        with app.app_context():
            # Re-query user to ensure it's attached to current session
            user = User.query.filter_by(email=admin_user.email).first()
            response = client.get(f"/api/v1/user/{user.id}", headers=auth_headers_user)
        assert response.status_code == 403
        assert "Forbidden" in response.json["detail"]


@pytest.mark.usefixtures("client", "superadmin_user", "admin_user", "regular_user")
class TestRoleValidation:
    """Test role validation and constraints"""

    def test_valid_roles_accepted(
        self, client, auth_headers_superadmin, sample_user_data
    ):
        """Test that valid roles are accepted"""
        import uuid

        valid_roles = ["USER", "ADMIN", "SUPERADMIN"]

        for role in valid_roles:
            user_data = sample_user_data.copy()
            user_data["role"] = role
            # Generate unique email for each test to avoid conflicts
            unique_id = uuid.uuid4().hex[:8]
            user_data["email"] = f"test-{role.lower()}-{unique_id}@example.com"

            response = client.post(
                "/api/v1/user",
                headers=auth_headers_superadmin,
                data=json.dumps(user_data),
                content_type="application/json",
            )
            print(
                f"Testing role {role}: status={response.status_code}, response={response.json}"
            )
            assert response.status_code == 200
            assert response.json["data"]["role"] == role

    def test_invalid_role_rejected(
        self, client, auth_headers_superadmin, sample_user_data
    ):
        """Test that invalid roles are rejected"""
        sample_user_data["role"] = "INVALID_ROLE"
        response = client.post(
            "/api/v1/user",
            headers=auth_headers_superadmin,
            data=json.dumps(sample_user_data),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "Invalid role" in response.json["detail"]

    def test_default_role_is_user(
        self, client, auth_headers_superadmin, sample_user_data
    ):
        """Test that default role is USER when not specified"""
        # Remove role from data
        if "role" in sample_user_data:
            del sample_user_data["role"]

        response = client.post(
            "/api/v1/user",
            headers=auth_headers_superadmin,
            data=json.dumps(sample_user_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json["data"]["role"] == "USER"

    def test_role_update_validation(
        self, client, auth_headers_superadmin, regular_user, app
    ):
        """Test role update validation"""
        with app.app_context():
            # Re-query user to ensure it's attached to current session
            user = User.query.filter_by(email=regular_user.email).first()

            # Valid role update
            update_data = {"role": "ADMIN"}
            response = client.patch(
                f"/api/v1/user/{user.id}",
                headers=auth_headers_superadmin,
                data=json.dumps(update_data),
                content_type="application/json",
            )
            assert response.status_code == 200
            assert response.json["data"]["role"] == "ADMIN"

            # Invalid role update
            update_data = {"role": "INVALID_ROLE"}
            response = client.patch(
                f"/api/v1/user/{user.id}",
                headers=auth_headers_superadmin,
                data=json.dumps(update_data),
                content_type="application/json",
            )
            assert response.status_code == 400
            assert "Invalid role" in response.json["detail"]


@pytest.mark.usefixtures("client", "superadmin_user", "admin_user", "regular_user")
class TestUserFiltering:
    """Test user filtering by role"""

    def test_filter_by_superadmin_role(self, client, auth_headers_superadmin):
        """Test filtering users by SUPERADMIN role"""
        response = client.get(
            "/api/v1/user?filter=role=SUPERADMIN", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        data = response.json["data"]
        for user in data:
            assert user["role"] == "SUPERADMIN"

    def test_filter_by_admin_role(self, client, auth_headers_superadmin):
        """Test filtering users by ADMIN role"""
        response = client.get(
            "/api/v1/user?filter=role=ADMIN", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        data = response.json["data"]
        for user in data:
            assert user["role"] == "ADMIN"

    def test_sort_by_role(self, client, auth_headers_superadmin):
        """Test sorting users by role"""
        response = client.get(
            "/api/v1/user?sort=role desc", headers=auth_headers_superadmin
        )
        assert response.status_code == 200
        data = response.json["data"]
        roles = [u["role"] for u in data]
        assert roles == sorted(roles, reverse=True)
