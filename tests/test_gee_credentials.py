"""Tests for GEE credentials functionality"""

import os
from unittest.mock import Mock, patch
import uuid

import pytest

from gefapi import db
from gefapi.models.user import User
from gefapi.services.gee_service import GEEService

# Ensure test environment has required variables
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-encryption")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key")
os.environ.setdefault("EE_SERVICE_ACCOUNT_JSON", "test-service-account-json")
os.environ.setdefault("GOOGLE_PROJECT_ID", "test-project-id")


def generate_unique_email(prefix="test"):
    """Generate a unique email for testing"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


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


@pytest.fixture
def admin_user_with_token(app):
    """Fixture providing admin user and authentication token"""
    with app.app_context():
        from flask_jwt_extended import create_access_token

        # Use unique email to avoid conflicts
        unique_id = str(uuid.uuid4())[:8]
        admin_user = User(
            email=f"admin-{unique_id}@example.com",
            password="password123",
            name="Admin User",
            country="Test Country",
            institution="Test Institution",
            role="ADMIN",
        )
        db.session.add(admin_user)
        db.session.commit()

        token = create_access_token(identity=admin_user.id)
        return admin_user, token


@pytest.fixture
def superadmin_user_with_token(app):
    """Fixture providing superadmin user and authentication token"""
    with app.app_context():
        from flask_jwt_extended import create_access_token

        # Use unique email to avoid conflicts
        unique_id = str(uuid.uuid4())[:8]
        superadmin_user = User(
            email=f"superadmin-{unique_id}@example.com",
            password="password123",
            name="Superadmin User",
            country="Test Country",
            institution="Test Institution",
            role="SUPERADMIN",
        )
        db.session.add(superadmin_user)
        db.session.commit()

        token = create_access_token(identity=superadmin_user.id)
        return superadmin_user, token


class TestUserGEECredentials:
    """Test User model GEE credential methods"""

    def test_user_has_no_gee_credentials_by_default(self, app_with_db):
        """Test that users have no GEE credentials by default"""
        with app_with_db.app_context():
            user = User(
                email=generate_unique_email("no-creds"),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
            )
            # Don't persist to database for this simple test
            assert not user.has_gee_credentials()
            assert user.gee_credentials_type is None
            assert user.gee_credentials_created_at is None

    def test_set_and_get_oauth_credentials(self, app_with_db):
        """Test setting and getting OAuth credentials"""
        with app_with_db.app_context():
            user = User(
                email=generate_unique_email("oauth-creds"),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
            )

            # Set OAuth credentials
            access_token = "test_access_token"
            refresh_token = "test_refresh_token"
            user.set_gee_oauth_credentials(access_token, refresh_token)

            assert user.has_gee_credentials()
            assert user.gee_credentials_type == "oauth"
            assert user.gee_credentials_created_at is not None

            # Get OAuth credentials
            retrieved_access, retrieved_refresh, _ = user.get_gee_oauth_credentials()
            assert retrieved_access == access_token
            assert retrieved_refresh == refresh_token

    def test_set_and_get_service_account_credentials(self, app_with_db):
        """Test setting and getting service account credentials"""
        with app_with_db.app_context():
            user = User(
                email=generate_unique_email(),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
            )

            # Set service account credentials
            service_account_key = {
                "type": "service_account",
                "project_id": "test_project",
                "private_key_id": "test_key_id",
                "private_key": "-----BEGIN PRIVATE KEY-----\ntest_key\n-----END PRIVATE KEY-----\n",
                "client_email": "test@test.iam.gserviceaccount.com",
                "client_id": "12345",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            user.set_gee_service_account(service_account_key)

            assert user.has_gee_credentials()
            assert user.gee_credentials_type == "service_account"
            assert user.gee_credentials_created_at is not None

            # Get service account credentials
            retrieved_key = user.get_gee_service_account()
            assert retrieved_key == service_account_key

    def test_clear_gee_credentials(self, app_with_db):
        """Test clearing GEE credentials"""
        with app_with_db.app_context():
            user = User(
                email=generate_unique_email(),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
            )

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
                email=generate_unique_email(),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
            )

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
                email=generate_unique_email(),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
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
            "token_uri": "https://oauth2.googleapis.com/token",
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
            "token_uri": "https://oauth2.googleapis.com/token",
        }

        assert not GEEService.validate_service_account_key(invalid_key)

    @patch("gefapi.services.gee_service.ee")
    def test_initialize_ee_with_user_oauth(self, mock_ee, app_with_db):
        """Test initializing GEE with user OAuth credentials"""
        with app_with_db.app_context():
            user = User(
                email=generate_unique_email(),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
            )
            user.set_gee_oauth_credentials("access_token", "refresh_token")
            db.session.add(user)
            db.session.commit()

            # Mock EE operations check to return initialized
            mock_ee.data.listOperations.return_value = [{"id": "test"}]

            result = GEEService._initialize_ee(user)
            assert result is True

    @patch("gefapi.services.gee_service.ee")
    def test_initialize_ee_with_user_service_account(self, mock_ee, app_with_db):
        """Test initializing GEE with user service account"""
        with app_with_db.app_context():
            user = User(
                email=generate_unique_email(),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
            )
            service_account_key = {
                "type": "service_account",
                "project_id": "test_project",
                "private_key_id": "test_key_id",
                "private_key": "-----BEGIN PRIVATE KEY-----\ntest_key\n-----END PRIVATE KEY-----\n",
                "client_email": "test@test.iam.gserviceaccount.com",
                "client_id": "12345",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            user.set_gee_service_account(service_account_key)
            db.session.add(user)
            db.session.commit()

            # Mock EE operations check to return initialized
            mock_ee.data.listOperations.return_value = [{"id": "test"}]

            result = GEEService._initialize_ee(user)
            assert result is True

    @patch("gefapi.services.gee_service.ee")
    def test_cancel_gee_task_with_user(self, mock_ee, app_with_db):
        """Test canceling GEE task with user credentials"""
        with app_with_db.app_context():
            user = User(
                email=generate_unique_email(),
                password="password123",
                name="Test User",
                country="Test Country",
                institution="Test Institution",
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
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["data"]["has_credentials"] is False
        assert data["data"]["credentials_type"] is None

    def test_get_gee_credentials_with_oauth(self, client, user_with_token, app_with_db):
        """Test getting GEE credentials status when user has OAuth"""
        user, token = user_with_token

        with app_with_db.app_context():
            # Re-query user to ensure it's in the current session
            user = User.query.get(user.id)
            user.set_gee_oauth_credentials("access_token", "refresh_token")
            db.session.commit()

        response = client.get(
            "/api/v1/user/me/gee-credentials",
            headers={"Authorization": f"Bearer {token}"},
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
            "token_uri": "https://oauth2.googleapis.com/token",
        }

        response = client.post(
            "/api/v1/user/me/gee-service-account",
            headers={"Authorization": f"Bearer {token}"},
            json={"service_account_key": service_account_key},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert "successfully" in data["message"].lower()

    def test_upload_service_account_invalid(self, client, user_with_token):
        """Test uploading invalid service account"""
        user, token = user_with_token

        invalid_key = {
            "type": "user_account",  # Wrong type
            "project_id": "test_project",
        }

        response = client.post(
            "/api/v1/user/me/gee-service-account",
            headers={"Authorization": f"Bearer {token}"},
            json={"service_account_key": invalid_key},
        )

        assert response.status_code == 400
        data = response.get_json()
        assert "invalid" in data["detail"].lower()

    def test_delete_gee_credentials(self, client, user_with_token, app_with_db):
        """Test deleting GEE credentials"""
        user, token = user_with_token

        with app_with_db.app_context():
            # Re-query user to ensure it's in the current session
            user = User.query.get(user.id)
            user.set_gee_oauth_credentials("access_token", "refresh_token")
            db.session.commit()

        response = client.delete(
            "/api/v1/user/me/gee-credentials",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert "GEE credentials deleted successfully" in data["message"]

    def test_delete_gee_credentials_none_exist(self, client, user_with_token):
        """Test deleting GEE credentials when none exist"""
        user, token = user_with_token

        response = client.delete(
            "/api/v1/user/me/gee-credentials",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404
        data = response.get_json()
        assert "No GEE credentials found" in data["detail"]

    @patch.dict(
        os.environ,
        {
            "GOOGLE_OAUTH_CLIENT_ID": "test_client_id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "test_client_secret",
        },
    )
    @patch("google_auth_oauthlib.flow.Flow")
    def test_initiate_oauth_flow(self, mock_flow, client, user_with_token):
        """Test initiating OAuth flow"""
        user, token = user_with_token

        # Mock the Flow
        mock_flow_instance = Mock()
        mock_flow_instance.authorization_url.return_value = (
            "https://accounts.google.com/oauth2/auth?...",
            "test_state",
        )
        mock_flow_instance.code_verifier = "test_pkce_verifier"
        mock_flow.from_client_config.return_value = mock_flow_instance

        response = client.post(
            "/api/v1/user/me/gee-oauth/initiate",
            headers={"Authorization": f"Bearer {token}"},
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
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 500
        data = response.get_json()
        assert "OAuth not configured" in data["detail"]

    def test_api_requires_authentication(self, client):
        """Test that all GEE credential endpoints require authentication"""
        # These endpoints should return 401 when accessed without auth
        get_endpoints = [
            "/api/v1/user/me/gee-credentials",
        ]

        # These endpoints only accept POST, so GET should return 405
        post_only_endpoints = [
            "/api/v1/user/me/gee-oauth/initiate",
            "/api/v1/user/me/gee-credentials/test",
            "/api/v1/user/me/gee-service-account",  # This is POST-only
        ]

        for endpoint in get_endpoints:
            response = client.get(endpoint)
            assert response.status_code == 401

        for endpoint in post_only_endpoints:
            response = client.get(endpoint)
            assert response.status_code == 405  # Method not allowed

            # Test POST without auth should return 401
            response = client.post(endpoint)
            assert response.status_code == 401


class TestAdminGEECredentialsAPI:
    """Test admin endpoints for managing other users' GEE credentials"""

    def test_admin_get_user_gee_credentials_no_credentials(
        self, client, admin_user_with_token, user_with_token
    ):
        """Test admin getting user's GEE credentials status when user has none"""
        admin_user, admin_token = admin_user_with_token
        target_user, _ = user_with_token
        target_user_id = target_user.id  # Store the ID before context switches
        target_user_email = target_user.email  # Store the email before context switches

        response = client.get(
            f"/api/v1/user/{target_user_id}/gee-credentials",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["data"]["user_id"] == str(target_user_id)
        assert data["data"]["user_email"] == target_user_email
        assert data["data"]["has_credentials"] is False
        assert data["data"]["credentials_type"] is None

    def test_admin_get_user_gee_credentials_with_oauth(
        self, client, admin_user_with_token, user_with_token, app_with_db
    ):
        """Test admin getting user's GEE credentials status when user has OAuth"""
        admin_user, admin_token = admin_user_with_token
        target_user, _ = user_with_token
        target_user_id = target_user.id  # Store the ID before context switches
        target_user_id = target_user_id  # Store the ID before the context

        with app_with_db.app_context():
            # Re-query user to ensure it's in the current session
            target_user = User.query.get(target_user_id)
            target_user.set_gee_oauth_credentials("access_token", "refresh_token")
            db.session.commit()

        response = client.get(
            f"/api/v1/user/{target_user_id}/gee-credentials",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["data"]["user_id"] == str(target_user_id)
        assert data["data"]["has_credentials"] is True
        assert data["data"]["credentials_type"] == "oauth"

    def test_admin_upload_service_account_for_user(
        self, client, admin_user_with_token, user_with_token
    ):
        """Test admin uploading service account for another user"""
        admin_user, admin_token = admin_user_with_token
        target_user, _ = user_with_token
        target_user_id = target_user.id  # Store the ID before context switches
        target_user_email = target_user.email  # Store the email before context switches

        service_account_key = {
            "type": "service_account",
            "project_id": "test_project",
            "private_key_id": "test_key_id",
            "private_key": "-----BEGIN PRIVATE KEY-----\ntest_key\n-----END PRIVATE KEY-----\n",
            "client_email": "test@test.iam.gserviceaccount.com",
            "client_id": "12345",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }

        response = client.post(
            f"/api/v1/user/{target_user_id}/gee-service-account",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_account_key": service_account_key},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert "saved for user" in data["message"]
        assert target_user_email in data["message"]

    def test_admin_upload_invalid_service_account_for_user(
        self, client, admin_user_with_token, user_with_token
    ):
        """Test admin uploading invalid service account for another user"""
        admin_user, admin_token = admin_user_with_token
        target_user, _ = user_with_token
        target_user_id = target_user.id  # Store the ID before context switches

        invalid_key = {
            "type": "user_account",  # Wrong type
            "project_id": "test_project",
        }

        response = client.post(
            f"/api/v1/user/{target_user_id}/gee-service-account",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_account_key": invalid_key},
        )

        assert response.status_code == 400
        data = response.get_json()
        assert "invalid" in data["detail"].lower()

    def test_admin_delete_user_gee_credentials(
        self, client, admin_user_with_token, user_with_token, app_with_db
    ):
        """Test admin deleting another user's GEE credentials"""
        admin_user, admin_token = admin_user_with_token
        target_user, _ = user_with_token
        target_user_id = target_user.id  # Store the ID before context switches
        target_user_email = target_user.email  # Store the email before context switches

        with app_with_db.app_context():
            # Re-query user to ensure it's in the current session
            target_user = User.query.get(target_user_id)
            target_user.set_gee_oauth_credentials("access_token", "refresh_token")
            db.session.commit()

        response = client.delete(
            f"/api/v1/user/{target_user_id}/gee-credentials",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert "deleted for user" in data["message"]
        assert target_user_email in data["message"]

    def test_admin_delete_user_gee_credentials_none_exist(
        self, client, admin_user_with_token, user_with_token
    ):
        """Test admin deleting user's GEE credentials when none exist"""
        admin_user, admin_token = admin_user_with_token
        target_user, _ = user_with_token
        target_user_id = target_user.id  # Store the ID before context switches

        response = client.delete(
            f"/api/v1/user/{target_user_id}/gee-credentials",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 404
        data = response.get_json()
        assert "No GEE credentials found for user" in data["detail"]

    @patch("gefapi.services.gee_service.GEEService._initialize_ee")
    def test_admin_test_user_gee_credentials_valid(
        self,
        mock_initialize,
        client,
        admin_user_with_token,
        user_with_token,
        app_with_db,
    ):
        """Test admin testing another user's valid GEE credentials"""
        admin_user, admin_token = admin_user_with_token
        target_user, _ = user_with_token
        target_user_id = target_user.id  # Store the ID before context switches
        target_user_email = target_user.email  # Store the email before context switches

        with app_with_db.app_context():
            # Re-query user to ensure it's in the current session
            target_user = User.query.get(target_user_id)
            target_user.set_gee_oauth_credentials("access_token", "refresh_token")
            db.session.commit()

        # Mock EE initialization to return True (valid credentials)
        mock_initialize.return_value = True

        response = client.post(
            f"/api/v1/user/{target_user_id}/gee-credentials/test",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert "are valid and working" in data["message"]
        assert target_user_email in data["message"]

    @patch("gefapi.services.gee_service.GEEService._initialize_ee")
    def test_admin_test_user_gee_credentials_invalid(
        self,
        mock_initialize,
        client,
        admin_user_with_token,
        user_with_token,
        app_with_db,
    ):
        """Test admin testing another user's invalid GEE credentials"""
        admin_user, admin_token = admin_user_with_token
        target_user, _ = user_with_token
        target_user_id = target_user.id  # Store the ID before context switches
        target_user_email = target_user.email  # Store the email before context switches

        with app_with_db.app_context():
            # Re-query user to ensure it's in the current session
            target_user = User.query.get(target_user_id)
            target_user.set_gee_oauth_credentials("access_token", "refresh_token")
            db.session.commit()

        # Mock EE initialization to return False (invalid credentials)
        mock_initialize.return_value = False

        response = client.post(
            f"/api/v1/user/{target_user_id}/gee-credentials/test",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 400
        data = response.get_json()
        assert "are invalid or expired" in data["detail"]
        assert target_user_email in data["detail"]

    def test_admin_test_user_gee_credentials_none_exist(
        self, client, admin_user_with_token, user_with_token
    ):
        """Test admin testing user's GEE credentials when none exist"""
        admin_user, admin_token = admin_user_with_token
        target_user, _ = user_with_token

        with client.application.app_context():
            from gefapi import db

            # Refresh the user from database to ensure we have the latest state
            fresh_user = User.query.get(target_user.id)
            # Ensure the user has no GEE credentials
            fresh_user.clear_gee_credentials()
            db.session.commit()

            target_user_id = fresh_user.id

        response = client.post(
            f"/api/v1/user/{target_user_id}/gee-credentials/test",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 400
        data = response.get_json()
        assert "No GEE credentials configured for user" in data["detail"]

    def test_admin_endpoints_user_not_found(self, client, admin_user_with_token):
        """Test admin endpoints with non-existent user ID"""
        admin_user, admin_token = admin_user_with_token
        non_existent_id = "12345678-1234-1234-1234-123456789012"

        endpoints = [
            ("GET", f"/api/v1/user/{non_existent_id}/gee-credentials"),
            ("POST", f"/api/v1/user/{non_existent_id}/gee-service-account"),
            ("DELETE", f"/api/v1/user/{non_existent_id}/gee-credentials"),
            ("POST", f"/api/v1/user/{non_existent_id}/gee-credentials/test"),
        ]

        for method, endpoint in endpoints:
            json_data = (
                {"service_account_key": {}}
                if method == "POST" and "service-account" in endpoint
                else None
            )
            if method == "GET":
                response = client.get(
                    endpoint, headers={"Authorization": f"Bearer {admin_token}"}
                )
            elif method == "POST":
                response = client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json=json_data,
                )
            elif method == "DELETE":
                response = client.delete(
                    endpoint, headers={"Authorization": f"Bearer {admin_token}"}
                )

            assert response.status_code == 404
            data = response.get_json()
            assert "User not found" in data["detail"]

    def test_regular_user_cannot_access_admin_endpoints(self, client, user_with_token):
        """Test that regular users cannot access admin endpoints"""
        user, token = user_with_token
        target_user_id = user.id  # Try to access their own account via admin endpoints

        endpoints = [
            ("GET", f"/api/v1/user/{target_user_id}/gee-credentials"),
            ("POST", f"/api/v1/user/{target_user_id}/gee-service-account"),
            ("DELETE", f"/api/v1/user/{target_user_id}/gee-credentials"),
            ("POST", f"/api/v1/user/{target_user_id}/gee-credentials/test"),
        ]

        for method, endpoint in endpoints:
            json_data = (
                {"service_account_key": {}}
                if method == "POST" and "service-account" in endpoint
                else None
            )
            if method == "GET":
                response = client.get(
                    endpoint, headers={"Authorization": f"Bearer {token}"}
                )
            elif method == "POST":
                response = client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {token}"},
                    json=json_data,
                )
            elif method == "DELETE":
                response = client.delete(
                    endpoint, headers={"Authorization": f"Bearer {token}"}
                )

            assert response.status_code == 403
            data = response.get_json()
            assert "Admin access required" in data["detail"]

    def test_superadmin_can_access_admin_endpoints(
        self, client, superadmin_user_with_token, user_with_token
    ):
        """Test that superadmin users can access admin endpoints"""
        superadmin_user, superadmin_token = superadmin_user_with_token
        target_user, _ = user_with_token
        target_user_id = target_user.id  # Store the ID before context switches

        response = client.get(
            f"/api/v1/user/{target_user_id}/gee-credentials",
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["data"]["user_id"] == str(target_user_id)

    def test_admin_endpoints_require_authentication(self, client, user_with_token):
        """Test that admin GEE credential endpoints require authentication"""
        target_user, _ = user_with_token
        target_user_id = target_user.id  # Store the ID before context switches

        endpoints = [
            ("GET", f"/api/v1/user/{target_user_id}/gee-credentials"),
            ("POST", f"/api/v1/user/{target_user_id}/gee-service-account"),
            ("DELETE", f"/api/v1/user/{target_user_id}/gee-credentials"),
            ("POST", f"/api/v1/user/{target_user_id}/gee-credentials/test"),
        ]

        for method, endpoint in endpoints:
            if method == "GET":
                response = client.get(endpoint)
            elif method == "POST":
                response = client.post(endpoint)
            elif method == "DELETE":
                response = client.delete(endpoint)

            assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tests for PATCH /user/me/gee-credentials/project — project_number + IAM
# ---------------------------------------------------------------------------


class TestSetGeeCloudProject:
    """Tests for the project-selection endpoint including IAM grant logic."""

    def _set_oauth_creds(self, app, user_id):
        """Helper: attach OAuth credentials to user identified by *user_id*."""
        with app.app_context():
            u = User.query.get(user_id)
            u.set_gee_oauth_credentials("access_token", "refresh_token")
            db.session.commit()

    def test_project_number_supplied_directly_grants_iam(
        self, client, user_with_token, app_with_db
    ):
        """When project_number is provided and CRM confirms it, IAM grant is called."""
        user, token = user_with_token
        self._set_oauth_creds(app_with_db, user.id)

        mock_crm_response = Mock()
        mock_crm_response.status_code = 200
        mock_crm_response.json.return_value = {
            "projectNumber": "123456789012",
            "projectId": "my-project",
        }

        with (
            patch(
                "gefapi.routes.api.v1.gee_credentials.AuthorizedSession"
            ) as mock_session_cls,
            patch(
                "gefapi.routes.api.v1.gee_credentials._provision_gee_service_agent"
            ) as mock_provision,
            patch(
                "gefapi.routes.api.v1.gee_credentials.grant_gee_service_agent_bucket_write"
            ) as mock_grant,
            patch(
                "gefapi.routes.api.v1.gee_credentials.revoke_gee_service_agent_bucket_write"
            ),
        ):
            mock_session = Mock()
            mock_session.get.return_value = mock_crm_response
            mock_session_cls.return_value = mock_session
            mock_provision.return_value = True
            mock_grant.return_value = True

            resp = client.patch(
                "/api/v1/user/me/gee-credentials/project",
                headers={"Authorization": f"Bearer {token}"},
                json={"cloud_project": "my-project", "project_number": 123456789012},
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["gcs_write_access"] is True
        mock_grant.assert_called_once()
        # Verify the user's project_number is persisted.
        with app_with_db.app_context():
            u = User.query.get(user.id)
            assert u.gee_cloud_project_number == 123456789012

    def test_project_number_mismatch_with_crm_returns_400(
        self, client, user_with_token, app_with_db
    ):
        """When supplied project_number doesn't match CRM response → 400 (ownership check)."""
        user, token = user_with_token
        self._set_oauth_creds(app_with_db, user.id)

        mock_crm_response = Mock()
        mock_crm_response.status_code = 200
        mock_crm_response.json.return_value = {
            "projectNumber": "999999999999",  # different from what user sent
            "projectId": "my-project",
        }

        with patch(
            "gefapi.routes.api.v1.gee_credentials.AuthorizedSession"
        ) as mock_session_cls:
            mock_session = Mock()
            mock_session.get.return_value = mock_crm_response
            mock_session_cls.return_value = mock_session

            resp = client.patch(
                "/api/v1/user/me/gee-credentials/project",
                headers={"Authorization": f"Bearer {token}"},
                json={"cloud_project": "my-project", "project_number": 123456789012},
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert "project_number" in data["detail"]

    def test_project_number_with_crm_403_uses_supplied_number(
        self, client, user_with_token, app_with_db
    ):
        """CRM 403 (scope missing) + manual project_number supplied → IAM grant proceeds."""
        user, token = user_with_token
        self._set_oauth_creds(app_with_db, user.id)

        mock_crm_response = Mock()
        mock_crm_response.status_code = 403

        with (
            patch(
                "gefapi.routes.api.v1.gee_credentials.AuthorizedSession"
            ) as mock_session_cls,
            patch(
                "gefapi.routes.api.v1.gee_credentials._provision_gee_service_agent"
            ) as mock_provision,
            patch(
                "gefapi.routes.api.v1.gee_credentials.grant_gee_service_agent_bucket_write"
            ) as mock_grant,
            patch(
                "gefapi.routes.api.v1.gee_credentials.revoke_gee_service_agent_bucket_write"
            ),
        ):
            mock_session = Mock()
            mock_session.get.return_value = mock_crm_response
            mock_session_cls.return_value = mock_session
            mock_provision.return_value = True
            mock_grant.return_value = True

            resp = client.patch(
                "/api/v1/user/me/gee-credentials/project",
                headers={"Authorization": f"Bearer {token}"},
                json={"cloud_project": "my-project", "project_number": 123456789012},
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["gcs_write_access"] is True
        mock_grant.assert_called_once()
        args = mock_grant.call_args[0]
        assert args[0] == 123456789012

    def test_changing_project_revokes_old_grant(
        self, client, user_with_token, app_with_db
    ):
        """Switching to a new project revokes the old service-agent binding first."""
        user, token = user_with_token
        with app_with_db.app_context():
            u = User.query.get(user.id)
            u.set_gee_oauth_credentials("access_token", "refresh_token")
            u.gee_cloud_project = "old-project"
            u.gee_cloud_project_number = 111111111111
            db.session.commit()

        mock_crm_response = Mock()
        mock_crm_response.status_code = 200
        mock_crm_response.json.return_value = {
            "projectNumber": "222222222222",
            "projectId": "new-project",
        }

        with (
            patch(
                "gefapi.routes.api.v1.gee_credentials.AuthorizedSession"
            ) as mock_session_cls,
            patch(
                "gefapi.routes.api.v1.gee_credentials._provision_gee_service_agent"
            ) as mock_provision,
            patch(
                "gefapi.routes.api.v1.gee_credentials.grant_gee_service_agent_bucket_write"
            ) as mock_grant,
            patch(
                "gefapi.routes.api.v1.gee_credentials.revoke_gee_service_agent_bucket_write"
            ) as mock_revoke,
        ):
            mock_session = Mock()
            mock_session.get.return_value = mock_crm_response
            mock_session_cls.return_value = mock_session
            mock_provision.return_value = True
            mock_grant.return_value = True

            resp = client.patch(
                "/api/v1/user/me/gee-credentials/project",
                headers={"Authorization": f"Bearer {token}"},
                json={"cloud_project": "new-project"},
            )

        assert resp.status_code == 200
        # Old project's service agent should have been revoked.
        mock_revoke.assert_called_once()
        revoke_args = mock_revoke.call_args[0]
        assert revoke_args[0] == 111111111111  # old number
        # New project's service agent should have been granted.
        mock_grant.assert_called_once()
        grant_args = mock_grant.call_args[0]
        assert grant_args[0] == 222222222222  # new number

    def test_crm_lookup_success_grants_iam(self, client, user_with_token, app_with_db):
        """When project_number is NOT supplied, the route calls CRM to resolve it."""
        user, token = user_with_token
        self._set_oauth_creds(app_with_db, user.id)

        mock_crm_response = Mock()
        mock_crm_response.status_code = 200
        mock_crm_response.json.return_value = {
            "projectNumber": "987654321098",
            "projectId": "my-project",
        }

        with (
            patch(
                "gefapi.routes.api.v1.gee_credentials.AuthorizedSession"
            ) as mock_session_cls,
            patch(
                "gefapi.routes.api.v1.gee_credentials._provision_gee_service_agent"
            ) as mock_provision,
            patch(
                "gefapi.routes.api.v1.gee_credentials.grant_gee_service_agent_bucket_write"
            ) as mock_grant,
        ):
            mock_session = Mock()
            mock_session.get.return_value = mock_crm_response
            mock_session_cls.return_value = mock_session
            mock_provision.return_value = True
            mock_grant.return_value = True

            resp = client.patch(
                "/api/v1/user/me/gee-credentials/project",
                headers={"Authorization": f"Bearer {token}"},
                json={"cloud_project": "my-project"},
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["gcs_write_access"] is True
        mock_grant.assert_called_once()
        args = mock_grant.call_args[0]
        assert args[0] == 987654321098  # numeric project number

    def test_crm_403_saves_project_without_iam(
        self, client, user_with_token, app_with_db
    ):
        """CRM 403 (scope missing) → project saved, gcs_write_access=False."""
        user, token = user_with_token
        self._set_oauth_creds(app_with_db, user.id)

        mock_crm_response = Mock()
        mock_crm_response.status_code = 403

        with (
            patch(
                "gefapi.routes.api.v1.gee_credentials.AuthorizedSession"
            ) as mock_session_cls,
            patch(
                "gefapi.routes.api.v1.gee_credentials.grant_gee_service_agent_bucket_write"
            ) as mock_grant,
        ):
            mock_session = Mock()
            mock_session.get.return_value = mock_crm_response
            mock_session_cls.return_value = mock_session

            resp = client.patch(
                "/api/v1/user/me/gee-credentials/project",
                headers={"Authorization": f"Bearer {token}"},
                json={"cloud_project": "my-project"},
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["gcs_write_access"] is False
        assert "detail" in data
        mock_grant.assert_not_called()
        # Project ID still saved.
        with app_with_db.app_context():
            u = User.query.get(user.id)
            assert u.gee_cloud_project == "my-project"

    def test_crm_404_returns_400(self, client, user_with_token, app_with_db):
        """CRM 404 → 400 error returned to caller."""
        user, token = user_with_token
        self._set_oauth_creds(app_with_db, user.id)

        mock_crm_response = Mock()
        mock_crm_response.status_code = 404

        with patch(
            "gefapi.routes.api.v1.gee_credentials.AuthorizedSession"
        ) as mock_session_cls:
            mock_session = Mock()
            mock_session.get.return_value = mock_crm_response
            mock_session_cls.return_value = mock_session

            resp = client.patch(
                "/api/v1/user/me/gee-credentials/project",
                headers={"Authorization": f"Bearer {token}"},
                json={"cloud_project": "nonexistent-project"},
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert "not found" in data["detail"].lower()

    def test_iam_grant_failure_returns_gcs_write_access_false(
        self, client, user_with_token, app_with_db
    ):
        """IAM grant failure → 200 with gcs_write_access=False, project still saved."""
        user, token = user_with_token
        self._set_oauth_creds(app_with_db, user.id)

        with (
            patch(
                "gefapi.routes.api.v1.gee_credentials._provision_gee_service_agent"
            ) as mock_provision,
            patch(
                "gefapi.routes.api.v1.gee_credentials.grant_gee_service_agent_bucket_write",
                return_value=False,
            ),
        ):
            mock_provision.return_value = True
            resp = client.patch(
                "/api/v1/user/me/gee-credentials/project",
                headers={"Authorization": f"Bearer {token}"},
                json={"cloud_project": "my-project", "project_number": 111222333444},
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["gcs_write_access"] is False
        assert "detail" in data

    def test_invalid_project_number_type_returns_400(
        self, client, user_with_token, app_with_db
    ):
        """Non-integer project_number in request body → 400."""
        user, token = user_with_token
        self._set_oauth_creds(app_with_db, user.id)

        resp = client.patch(
            "/api/v1/user/me/gee-credentials/project",
            headers={"Authorization": f"Bearer {token}"},
            json={"cloud_project": "my-project", "project_number": "not-a-number"},
        )

        assert resp.status_code == 400
        data = resp.get_json()
        assert "project_number" in data["detail"]

    def test_invalid_cloud_project_format_returns_400(
        self, client, user_with_token, app_with_db
    ):
        """cloud_project with disallowed characters → 400 (prevents URL injection)."""
        user, token = user_with_token
        self._set_oauth_creds(app_with_db, user.id)

        for bad_value in [
            "MY-PROJECT",  # uppercase
            "my project",  # space
            "a",  # too short
            "my-project/../../other",  # path traversal
            "my-project?key=val",  # query injection
        ]:
            resp = client.patch(
                "/api/v1/user/me/gee-credentials/project",
                headers={"Authorization": f"Bearer {token}"},
                json={"cloud_project": bad_value},
            )
            assert resp.status_code == 400, (
                f"Expected 400 for cloud_project={bad_value!r}"
            )
            data = resp.get_json()
            assert "cloud_project" in data["detail"]

    def test_project_number_out_of_range_returns_400(
        self, client, user_with_token, app_with_db
    ):
        """project_number outside the valid GCP range → 400."""
        user, token = user_with_token
        self._set_oauth_creds(app_with_db, user.id)

        for bad_number in [0, -1, 10**14]:
            resp = client.patch(
                "/api/v1/user/me/gee-credentials/project",
                headers={"Authorization": f"Bearer {token}"},
                json={"cloud_project": "my-project", "project_number": bad_number},
            )
            assert resp.status_code == 400, (
                f"Expected 400 for project_number={bad_number}"
            )
            data = resp.get_json()
            assert "project_number" in data["detail"]

    def test_delete_credentials_calls_iam_revoke(
        self, client, user_with_token, app_with_db
    ):
        """Deleting credentials should trigger IAM revoke when project_number is set."""
        user, token = user_with_token

        with app_with_db.app_context():
            u = User.query.get(user.id)
            u.set_gee_oauth_credentials("access_token", "refresh_token")
            u.gee_cloud_project = "my-project"
            u.gee_cloud_project_number = 123456789012
            db.session.commit()

        with patch(
            "gefapi.routes.api.v1.gee_credentials.revoke_gee_service_agent_bucket_write"
        ) as mock_revoke:
            resp = client.delete(
                "/api/v1/user/me/gee-credentials",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        mock_revoke.assert_called_once()
        args = mock_revoke.call_args[0]
        assert args[0] == 123456789012

    def test_delete_credentials_no_project_number_skips_revoke(
        self, client, user_with_token, app_with_db
    ):
        """Deleting credentials without a project_number skips IAM revoke."""
        user, token = user_with_token

        with app_with_db.app_context():
            u = User.query.get(user.id)
            u.set_gee_oauth_credentials("access_token", "refresh_token")
            # No gee_cloud_project_number set
            db.session.commit()

        with patch(
            "gefapi.routes.api.v1.gee_credentials.revoke_gee_service_agent_bucket_write"
        ) as mock_revoke:
            resp = client.delete(
                "/api/v1/user/me/gee-credentials",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        mock_revoke.assert_not_called()


# ---------------------------------------------------------------------------
# Unit tests for gefapi/services/gcs_iam_service.py
# ---------------------------------------------------------------------------


@pytest.mark.standalone
class TestGcsIamService:
    """Unit tests for grant/revoke IAM helpers."""

    _SA_JSON = {
        "type": "service_account",
        "project_id": "ci-project",
        "private_key_id": "key1",
        "private_key": (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA0Z3VS5JJcds3xHn/ygWep4\n"
            "-----END RSA PRIVATE KEY-----\n"
        ),
        "client_email": "ci-sa@ci-project.iam.gserviceaccount.com",
        "client_id": "1",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }

    def _b64_sa(self):
        import base64
        import json

        return base64.b64encode(json.dumps(self._SA_JSON).encode()).decode()

    def test_grant_calls_set_iam_policy(self):
        """grant_gee_service_agent_bucket_write should set IAM policy with correct member."""

        from gefapi.services.gcs_iam_service import grant_gee_service_agent_bucket_write

        mock_bucket = Mock()
        mock_policy = Mock()
        mock_policy.bindings = []
        mock_bucket.get_iam_policy.return_value = mock_policy

        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket

        b64_sa = self._b64_sa()

        with (
            patch(
                "gefapi.services.gcs_iam_service.SETTINGS",
                {"environment": {"EE_SERVICE_ACCOUNT_JSON": b64_sa}},
            ),
            patch("google.cloud.storage.Client", return_value=mock_client),
            patch(
                "google.oauth2.service_account.Credentials.from_service_account_info"
            ),
        ):
            grant_gee_service_agent_bucket_write(123456789012, "ldmt")

        mock_bucket.set_iam_policy.assert_called_once()
        added = mock_policy.bindings[0]
        assert added["role"] == "roles/storage.objectCreator"
        expected_member = "serviceAccount:service-123456789012@gcp-sa-earthengine.iam.gserviceaccount.com"
        assert expected_member in added["members"]

    def test_grant_idempotent_when_binding_exists(self):
        """grant should not call set_iam_policy when binding already present."""
        from gefapi.services.gcs_iam_service import grant_gee_service_agent_bucket_write

        member = "serviceAccount:service-99@gcp-sa-earthengine.iam.gserviceaccount.com"
        mock_bucket = Mock()
        mock_policy = Mock()
        mock_policy.bindings = [
            {"role": "roles/storage.objectCreator", "members": {member}}
        ]
        mock_bucket.get_iam_policy.return_value = mock_policy

        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket

        b64_sa = self._b64_sa()

        with (
            patch(
                "gefapi.services.gcs_iam_service.SETTINGS",
                {"environment": {"EE_SERVICE_ACCOUNT_JSON": b64_sa}},
            ),
            patch("google.cloud.storage.Client", return_value=mock_client),
            patch(
                "google.oauth2.service_account.Credentials.from_service_account_info"
            ),
        ):
            grant_gee_service_agent_bucket_write(99, "ldmt")

        mock_bucket.set_iam_policy.assert_not_called()

    def test_revoke_removes_member(self):
        """revoke_gee_service_agent_bucket_write should remove member and call set_iam_policy."""
        from gefapi.services.gcs_iam_service import (
            revoke_gee_service_agent_bucket_write,
        )

        member = "serviceAccount:service-42@gcp-sa-earthengine.iam.gserviceaccount.com"
        mock_bucket = Mock()
        mock_policy = Mock()
        mock_policy.bindings = [
            {"role": "roles/storage.objectCreator", "members": {member}}
        ]
        mock_bucket.get_iam_policy.return_value = mock_policy

        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket

        b64_sa = self._b64_sa()

        with (
            patch(
                "gefapi.services.gcs_iam_service.SETTINGS",
                {"environment": {"EE_SERVICE_ACCOUNT_JSON": b64_sa}},
            ),
            patch("google.cloud.storage.Client", return_value=mock_client),
            patch(
                "google.oauth2.service_account.Credentials.from_service_account_info"
            ),
        ):
            revoke_gee_service_agent_bucket_write(42, "ldmt")

        mock_bucket.set_iam_policy.assert_called_once()
        # Binding with no remaining members should be excluded.
        assert mock_policy.bindings == []

    def test_revoke_idempotent_when_no_binding(self):
        """revoke should not call set_iam_policy when there is no matching binding."""
        from gefapi.services.gcs_iam_service import (
            revoke_gee_service_agent_bucket_write,
        )

        mock_bucket = Mock()
        mock_policy = Mock()
        mock_policy.bindings = []
        mock_bucket.get_iam_policy.return_value = mock_policy

        mock_client = Mock()
        mock_client.bucket.return_value = mock_bucket

        b64_sa = self._b64_sa()

        with (
            patch(
                "gefapi.services.gcs_iam_service.SETTINGS",
                {"environment": {"EE_SERVICE_ACCOUNT_JSON": b64_sa}},
            ),
            patch("google.cloud.storage.Client", return_value=mock_client),
            patch(
                "google.oauth2.service_account.Credentials.from_service_account_info"
            ),
        ):
            revoke_gee_service_agent_bucket_write(55, "ldmt")

        mock_bucket.set_iam_policy.assert_not_called()

    def test_missing_service_account_json_logs_warning(self, caplog):
        """When EE_SERVICE_ACCOUNT_JSON is absent the helpers log a warning and return."""
        import logging

        from gefapi.services.gcs_iam_service import grant_gee_service_agent_bucket_write

        with (
            patch("gefapi.services.gcs_iam_service.SETTINGS", {"environment": {}}),
            patch.dict(os.environ, {}, clear=True),
            caplog.at_level(logging.WARNING, logger="gefapi.services.gcs_iam_service"),
        ):
            # Should not raise.
            grant_gee_service_agent_bucket_write(1, "ldmt")

        assert any("EE_SERVICE_ACCOUNT_JSON" in r.message for r in caplog.records)
