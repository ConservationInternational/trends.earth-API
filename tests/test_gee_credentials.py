"""Tests for GEE credentials functionality"""

import json
import os
from unittest.mock import Mock, patch
import pytest

from gefapi import db
from gefapi.models.user import User
from gefapi.services.gee_service import GEEService


@pytest.fixture
def app_with_db(app):
    """Fixture providing app with database"""
    return app


@pytest.fixture
def user_with_token(app, regular_user):
    """Fixture providing user and authentication token"""
    with app.app_context():
        from flask_jwt_extended import create_access_token
        token = create_access_token(identity=regular_user.id)
        return regular_user, token


class TestUserGEECredentials:
    """Test User model GEE credential methods"""

    def test_user_has_no_gee_credentials_by_default(self, app_with_db):
        """Test that users have no GEE credentials by default"""
        with app_with_db.app_context():
            user = User(
                email="test@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution"
            )
            db.session.add(user)
            db.session.commit()

            assert not user.has_gee_credentials()
            assert user.gee_credentials_type is None
            assert user.gee_credentials_created_at is None

    def test_set_and_get_oauth_credentials(self, app_with_db):
        """Test setting and getting OAuth credentials"""
        with app_with_db.app_context():
            user = User(
                email="test@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution"
            )
            db.session.add(user)
            db.session.commit()

            # Set OAuth credentials
            access_token = "test_access_token"
            refresh_token = "test_refresh_token"
            user.set_gee_oauth_credentials(access_token, refresh_token)

            assert user.has_gee_credentials()
            assert user.gee_credentials_type == 'oauth'
            assert user.gee_credentials_created_at is not None

            # Get OAuth credentials
            retrieved_access, retrieved_refresh = user.get_gee_oauth_credentials()
            assert retrieved_access == access_token
            assert retrieved_refresh == refresh_token

    def test_set_and_get_service_account_credentials(self, app_with_db):
        """Test setting and getting service account credentials"""
        with app_with_db.app_context():
            user = User(
                email="test@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution"
            )
            db.session.add(user)
            db.session.commit()

            # Set service account credentials
            service_account_key = {
                "type": "service_account",
                "project_id": "test_project",
                "private_key_id": "test_key_id",
                "private_key": "-----BEGIN PRIVATE KEY-----\ntest_key\n-----END PRIVATE KEY-----\n",
                "client_email": "test@test.iam.gserviceaccount.com",
                "client_id": "12345",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
            user.set_gee_service_account(service_account_key)

            assert user.has_gee_credentials()
            assert user.gee_credentials_type == 'service_account'
            assert user.gee_credentials_created_at is not None

            # Get service account credentials
            retrieved_key = user.get_gee_service_account()
            assert retrieved_key == service_account_key

    def test_clear_gee_credentials(self, app_with_db):
        """Test clearing GEE credentials"""
        with app_with_db.app_context():
            user = User(
                email="test@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution"
            )
            db.session.add(user)
            db.session.commit()

            # Set credentials first
            user.set_gee_oauth_credentials("token", "refresh")
            assert user.has_gee_credentials()

            # Clear credentials
            user.clear_gee_credentials()
            assert not user.has_gee_credentials()
            assert user.gee_credentials_type is None
            assert user.gee_credentials_created_at is None

    def test_credential_encryption(self, app_with_db):
        """Test that credentials are encrypted in database"""
        with app_with_db.app_context():
            user = User(
                email="test@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution"
            )
            db.session.add(user)
            db.session.commit()

            # Set OAuth credentials
            access_token = "secret_access_token"
            refresh_token = "secret_refresh_token"
            user.set_gee_oauth_credentials(access_token, refresh_token)

            # Check that raw stored values are encrypted (not plaintext)
            assert user.gee_oauth_token != access_token
            assert user.gee_refresh_token != refresh_token
            assert access_token not in user.gee_oauth_token
            assert refresh_token not in user.gee_refresh_token

    def test_serialization_includes_gee_credentials(self, app_with_db):
        """Test that user serialization includes GEE credentials when requested"""
        with app_with_db.app_context():
            user = User(
                email="test@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution"
            )
            db.session.add(user)
            db.session.commit()

            # Without credentials
            user_data = user.serialize(include=["gee_credentials"])
            assert "gee_credentials" in user_data
            assert not user_data["gee_credentials"]["has_credentials"]

            # With credentials
            user.set_gee_oauth_credentials("token", "refresh")
            user_data = user.serialize(include=["gee_credentials"])
            assert user_data["gee_credentials"]["has_credentials"]
            assert user_data["gee_credentials"]["credentials_type"] == "oauth"


class TestGEEService:
    """Test GEE service functionality"""

    def test_validate_service_account_key_valid(self):
        """Test validation of valid service account key"""
        valid_key = {
            "type": "service_account",
            "project_id": "test_project",
            "private_key_id": "test_key_id",
            "private_key": "-----BEGIN PRIVATE KEY-----\ntest_key\n-----END PRIVATE KEY-----\n",
            "client_email": "test@test.iam.gserviceaccount.com",
            "client_id": "12345",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
        
        assert GEEService.validate_service_account_key(valid_key)

    def test_validate_service_account_key_missing_fields(self):
        """Test validation fails for service account key with missing fields"""
        invalid_key = {
            "type": "service_account",
            "project_id": "test_project",
            # Missing required fields
        }
        
        assert not GEEService.validate_service_account_key(invalid_key)

    def test_validate_service_account_key_wrong_type(self):
        """Test validation fails for wrong account type"""
        invalid_key = {
            "type": "user_account",  # Wrong type
            "project_id": "test_project",
            "private_key_id": "test_key_id",
            "private_key": "-----BEGIN PRIVATE KEY-----\ntest_key\n-----END PRIVATE KEY-----\n",
            "client_email": "test@test.iam.gserviceaccount.com",
            "client_id": "12345",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
        
        assert not GEEService.validate_service_account_key(invalid_key)

    @patch('gefapi.services.gee_service.ee')
    def test_initialize_ee_with_user_oauth(self, mock_ee, app_with_db):
        """Test initializing GEE with user OAuth credentials"""
        with app_with_db.app_context():
            user = User(
                email="test@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution"
            )
            user.set_gee_oauth_credentials("access_token", "refresh_token")
            db.session.add(user)
            db.session.commit()

            # Mock EE operations check to return initialized
            mock_ee.data.listOperations.return_value = [{"id": "test"}]
            
            result = GEEService._initialize_ee(user)
            assert result is True

    @patch('gefapi.services.gee_service.ee')
    def test_initialize_ee_with_user_service_account(self, mock_ee, app_with_db):
        """Test initializing GEE with user service account"""
        with app_with_db.app_context():
            user = User(
                email="test@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution"
            )
            service_account_key = {
                "type": "service_account",
                "project_id": "test_project",
                "private_key_id": "test_key_id",
                "private_key": "-----BEGIN PRIVATE KEY-----\ntest_key\n-----END PRIVATE KEY-----\n",
                "client_email": "test@test.iam.gserviceaccount.com",
                "client_id": "12345",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
            user.set_gee_service_account(service_account_key)
            db.session.add(user)
            db.session.commit()

            # Mock EE operations check to return initialized
            mock_ee.data.listOperations.return_value = [{"id": "test"}]
            
            result = GEEService._initialize_ee(user)
            assert result is True

    @patch('gefapi.services.gee_service.ee')
    def test_cancel_gee_task_with_user(self, mock_ee, app_with_db):
        """Test canceling GEE task with user credentials"""
        with app_with_db.app_context():
            user = User(
                email="test@example.com",
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution"
            )
            user.set_gee_oauth_credentials("access_token", "refresh_token")
            db.session.add(user)
            db.session.commit()

            # Mock EE operations
            mock_ee.data.listOperations.return_value = [{"id": "test"}]
            mock_ee.data.getOperation.return_value = {"done": False}
            mock_ee.data.cancelOperation.return_value = None

            result = GEEService.cancel_gee_task("TEST123456789012345678901234", user)
            assert result["success"] is True
            assert result["status"] == "CANCELLED"


class TestGEECredentialsAPI:
    """Test GEE credentials API endpoints"""

    def test_get_gee_credentials_no_credentials(self, client, user_with_token):
        """Test getting GEE credentials status when user has none"""
        user, token = user_with_token
        
        response = client.get(
            "/api/v1/user/me/gee-credentials",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        data = response.get_json()
        assert data["data"]["has_credentials"] is False
        assert data["data"]["credentials_type"] is None

    def test_get_gee_credentials_with_oauth(self, client, user_with_token, app_with_db):
        """Test getting GEE credentials status when user has OAuth"""
        user, token = user_with_token
        
        with app_with_db.app_context():
            user.set_gee_oauth_credentials("access_token", "refresh_token")
            db.session.commit()
        
        response = client.get(
            "/api/v1/user/me/gee-credentials",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        data = response.get_json()
        assert data["data"]["has_credentials"] is True
        assert data["data"]["credentials_type"] == "oauth"

    def test_upload_service_account_valid(self, client, user_with_token):
        """Test uploading valid service account"""
        user, token = user_with_token
        
        service_account_key = {
            "type": "service_account",
            "project_id": "test_project",
            "private_key_id": "test_key_id",
            "private_key": "-----BEGIN PRIVATE KEY-----\ntest_key\n-----END PRIVATE KEY-----\n",
            "client_email": "test@test.iam.gserviceaccount.com",
            "client_id": "12345",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
        
        response = client.post(
            "/api/v1/user/me/gee-service-account",
            headers={"Authorization": f"Bearer {token}"},
            json={"service_account_key": service_account_key}
        )
        
        assert response.status_code == 200
        data = response.get_json()
        assert "successfully" in data["message"].lower()

    def test_upload_service_account_invalid(self, client, user_with_token):
        """Test uploading invalid service account"""
        user, token = user_with_token
        
        invalid_key = {
            "type": "user_account",  # Wrong type
            "project_id": "test_project"
        }
        
        response = client.post(
            "/api/v1/user/me/gee-service-account",
            headers={"Authorization": f"Bearer {token}"},
            json={"service_account_key": invalid_key}
        )
        
        assert response.status_code == 400
        data = response.get_json()
        assert "invalid" in data["detail"].lower()

    def test_delete_gee_credentials(self, client, user_with_token, app_with_db):
        """Test deleting GEE credentials"""
        user, token = user_with_token
        
        with app_with_db.app_context():
            user.set_gee_oauth_credentials("access_token", "refresh_token")
            db.session.commit()
        
        response = client.delete(
            "/api/v1/user/me/gee-credentials",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        data = response.get_json()
        assert "deleted" in data["message"].lower()

    def test_delete_gee_credentials_none_exist(self, client, user_with_token):
        """Test deleting GEE credentials when none exist"""
        user, token = user_with_token
        
        response = client.delete(
            "/api/v1/user/me/gee-credentials",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 404
        data = response.get_json()
        assert "not found" in data["detail"].lower()

    @patch.dict(os.environ, {
        'GOOGLE_OAUTH_CLIENT_ID': 'test_client_id',
        'GOOGLE_OAUTH_CLIENT_SECRET': 'test_client_secret'
    })
    @patch('gefapi.routes.api.v1.gee_credentials.Flow')
    def test_initiate_oauth_flow(self, mock_flow, client, user_with_token):
        """Test initiating OAuth flow"""
        user, token = user_with_token
        
        # Mock the Flow
        mock_flow_instance = Mock()
        mock_flow_instance.authorization_url.return_value = (
            "https://accounts.google.com/oauth2/auth?...", "test_state"
        )
        mock_flow.from_client_config.return_value = mock_flow_instance
        
        response = client.post(
            "/api/v1/user/me/gee-oauth/initiate",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        data = response.get_json()
        assert "auth_url" in data["data"]
        assert "state" in data["data"]

    def test_initiate_oauth_flow_not_configured(self, client, user_with_token):
        """Test initiating OAuth flow when not configured"""
        user, token = user_with_token
        
        # Remove OAuth environment variables
        with patch.dict(os.environ, {}, clear=True):
            response = client.post(
                "/api/v1/user/me/gee-oauth/initiate",
                headers={"Authorization": f"Bearer {token}"}
            )
        
        assert response.status_code == 500
        data = response.get_json()
        assert "not configured" in data["detail"].lower()

    def test_api_requires_authentication(self, client):
        """Test that all GEE credential endpoints require authentication"""
        endpoints = [
            "/api/v1/user/me/gee-credentials",
            "/api/v1/user/me/gee-oauth/initiate",
            "/api/v1/user/me/gee-service-account",
            "/api/v1/user/me/gee-credentials/test"
        ]
        
        for endpoint in endpoints:
            response = client.get(endpoint)
            assert response.status_code == 401