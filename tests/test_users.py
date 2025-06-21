"""
Tests for user management endpoints
"""

import pytest


class TestUsers:
    """Test user management functionality"""

    def test_create_user(self, client):
        """Test user creation"""
        user_data = {
            "email": "newuser@test.com",
            "password": "password123",
            "name": "New User",
            "country": "Test Country",
            "institution": "Test Institution",
        }

        response = client.post("/api/v1/user", json=user_data)

        assert response.status_code == 200
        data = response.json["data"]
        assert data["email"] == "newuser@test.com"
        assert data["name"] == "New User"
        assert data["role"] == "USER"  # Default role
        assert "password" not in data  # Password should not be returned

    def test_create_user_duplicate_email(self, client, regular_user):
        """Test user creation with duplicate email"""
        user_data = {
            "email": "user@test.com",  # Already exists
            "password": "password123",
            "name": "Duplicate User",
        }

        response = client.post("/api/v1/user", json=user_data)
        assert response.status_code == 400

    def test_create_user_missing_required_fields(self, client):
        """Test user creation with missing required fields"""
        # Missing email
        response = client.post(
            "/api/v1/user", json={"password": "password123", "name": "Test User"}
        )
        assert response.status_code == 400

        # Missing password
        response = client.post(
            "/api/v1/user", json={"email": "test@test.com", "name": "Test User"}
        )
        assert response.status_code == 400

    def test_get_users_admin_only(self, client, auth_headers_admin, auth_headers_user):
        """Test getting all users (admin only)"""
        # Admin should be able to get users
        response = client.get("/api/v1/user", headers=auth_headers_admin)
        assert response.status_code == 200
        assert "data" in response.json

        # Regular user should be forbidden
        response = client.get("/api/v1/user", headers=auth_headers_user)
        assert response.status_code == 403

        # Unauthenticated should be unauthorized
        response = client.get("/api/v1/user")
        assert response.status_code == 401

    def test_get_user_by_id_admin_only(
        self, client, admin_user, regular_user, auth_headers_admin, auth_headers_user
    ):
        """Test getting specific user by ID (admin only)"""
        user_id = str(regular_user.id)

        # Admin should be able to get user
        response = client.get(f"/api/v1/user/{user_id}", headers=auth_headers_admin)
        assert response.status_code == 200
        assert response.json["data"]["email"] == "user@test.com"

        # Regular user should be forbidden
        response = client.get(f"/api/v1/user/{user_id}", headers=auth_headers_user)
        assert response.status_code == 403

    def test_get_me(self, client, auth_headers_user):
        """Test getting current user profile"""
        response = client.get("/api/v1/user/me", headers=auth_headers_user)

        assert response.status_code == 200
        data = response.json["data"]
        assert data["email"] == "user@test.com"
        assert data["name"] == "Regular User"

    def test_update_me(self, client, auth_headers_user):
        """Test updating current user profile"""
        update_data = {
            "name": "Updated Name",
            "country": "Updated Country",
            "institution": "Updated Institution",
        }

        response = client.patch(
            "/api/v1/user/me", json=update_data, headers=auth_headers_user
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["name"] == "Updated Name"
        assert data["country"] == "Updated Country"
        assert data["institution"] == "Updated Institution"

    def test_update_user_admin_only(
        self, client, regular_user, auth_headers_admin, auth_headers_user
    ):
        """Test updating user by ID (admin only)"""
        user_id = str(regular_user.id)
        update_data = {"name": "Admin Updated Name", "role": "ADMIN"}

        # Admin should be able to update user
        response = client.patch(
            f"/api/v1/user/{user_id}", json=update_data, headers=auth_headers_admin
        )
        assert response.status_code == 200
        assert response.json["data"]["name"] == "Admin Updated Name"

        # Regular user should be forbidden
        response = client.patch(
            f"/api/v1/user/{user_id}", json=update_data, headers=auth_headers_user
        )
        assert response.status_code == 403

    def test_delete_user_admin_only(
        self, client, regular_user, auth_headers_admin, auth_headers_user
    ):
        """Test deleting user by ID (admin only)"""
        user_id = str(regular_user.id)

        # Regular user should be forbidden
        response = client.delete(f"/api/v1/user/{user_id}", headers=auth_headers_user)
        assert response.status_code == 403

        # Admin should be able to delete user
        response = client.delete(f"/api/v1/user/{user_id}", headers=auth_headers_admin)
        assert response.status_code == 200

    def test_delete_me(self, client, auth_headers_user):
        """Test deleting own account"""
        response = client.delete("/api/v1/user/me", headers=auth_headers_user)
        assert response.status_code == 200

    def test_password_recovery(self, client, regular_user):
        """Test password recovery"""
        user_id = str(regular_user.id)
        response = client.post(f"/api/v1/user/{user_id}/recover-password")

        # Should succeed (email sending is mocked)
        assert response.status_code == 200
