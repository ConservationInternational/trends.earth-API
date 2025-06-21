"""
Tests for authentication endpoints
"""

import pytest


class TestAuth:
    """Test authentication functionality"""

    def test_successful_login(self, client, admin_user):
        """Test successful user login"""
        response = client.post(
            "/auth", json={"email": "admin@test.com", "password": "admin123"}
        )

        assert response.status_code == 200
        data = response.json
        assert "access_token" in data
        assert data["user"]["email"] == "admin@test.com"
        assert data["user"]["role"] == "ADMIN"

    def test_invalid_credentials(self, client, admin_user):
        """Test login with invalid credentials"""
        response = client.post(
            "/auth", json={"email": "admin@test.com", "password": "wrongpassword"}
        )

        assert response.status_code == 401

    def test_missing_credentials(self, client):
        """Test login with missing credentials"""
        # Missing password
        response = client.post("/auth", json={"email": "admin@test.com"})
        assert response.status_code == 400

        # Missing email
        response = client.post("/auth", json={"password": "admin123"})
        assert response.status_code == 400

        # Empty payload
        response = client.post("/auth", json={})
        assert response.status_code == 400

    def test_nonexistent_user(self, client):
        """Test login with non-existent user"""
        response = client.post(
            "/auth", json={"email": "nonexistent@test.com", "password": "password123"}
        )

        assert response.status_code == 401
