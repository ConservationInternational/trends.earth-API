"""Tests for Google Groups integration"""

from datetime import datetime
import json
from unittest.mock import Mock, patch

import pytest

from gefapi import create_app, db
from gefapi.models import User
from gefapi.services.google_groups_service import GoogleGroupsService


class TestGoogleGroupsIntegration:
    """Test Google Groups integration functionality"""

    @pytest.fixture
    def app(self):
        """Create test app"""
        app = create_app()
        app.config["TESTING"] = True
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"

        with app.app_context():
            db.create_all()
            yield app
            db.drop_all()

    @pytest.fixture
    def test_user(self, app):
        """Create test user"""
        with app.app_context():
            user = User(
                email="test@example.com",
                password="password123",
                name="Test User",
                country="US",
                institution="Test Org",
            )
            db.session.add(user)
            db.session.commit()
            return user

    def test_user_model_google_groups_fields(self, app, test_user):
        """Test that user model has Google Groups fields"""
        with app.app_context():
            user = User.query.filter_by(email="test@example.com").first()

            # Check default values
            assert user.google_groups_trends_earth_users is False
            assert user.google_groups_trendsearth is False
            assert user.google_groups_registration_status is None
            assert user.google_groups_last_sync is None

    def test_user_serialize_includes_google_groups(self, app, test_user):
        """Test user serialization includes Google Groups data"""
        with app.app_context():
            user = User.query.filter_by(email="test@example.com").first()

            # Update some Google Groups preferences
            user.google_groups_trends_earth_users = True
            user.google_groups_last_sync = datetime.utcnow()
            db.session.commit()

            # Serialize with Google Groups included
            serialized = user.serialize(include=["google_groups"])

            assert "google_groups" in serialized
            assert serialized["google_groups"]["trends_earth_users"] is True
            assert serialized["google_groups"]["trendsearth"] is False
            assert serialized["google_groups"]["last_sync"] is not None

    @patch("gefapi.services.google_groups_service.service_account")
    @patch("gefapi.services.google_groups_service.build")
    def test_google_groups_service_initialization(
        self, mock_build, mock_service_account
    ):
        """Test Google Groups service initialization"""
        # Mock successful initialization
        mock_credentials = Mock()
        mock_service_account.Credentials.from_service_account_info.return_value = (
            mock_credentials
        )
        mock_service = Mock()
        mock_build.return_value = mock_service

        service = GoogleGroupsService()
        service._initialize_service()

        # Should attempt to create credentials and service
        mock_service_account.Credentials.from_service_account_info.assert_called()
        mock_build.assert_called()

    def test_google_groups_service_add_user_success(self):
        """Test adding user to Google Group successfully"""
        service = GoogleGroupsService()

        # Mock successful service
        mock_service = Mock()
        mock_result = {"id": "member_123", "email": "test@example.com"}
        mock_service.members().insert().execute.return_value = mock_result
        service.service = mock_service

        result = service.add_user_to_group("test@example.com", "trends_earth_users")

        assert result["success"] is True
        assert result["group"] == "trends_earth_users"
        assert "member_id" in result

    def test_google_groups_service_add_user_already_member(self):
        """Test adding user who is already a member"""
        from googleapiclient.errors import HttpError

        service = GoogleGroupsService()

        # Mock service with conflict error (409)
        mock_service = Mock()
        mock_response = Mock()
        mock_response.status = 409
        error = HttpError(mock_response, b'{"error": "Member already exists"}')
        mock_service.members().insert().execute.side_effect = error
        service.service = mock_service

        result = service.add_user_to_group("test@example.com", "trends_earth_users")

        assert result["success"] is True
        assert result["already_member"] is True

    def test_google_groups_service_sync_user(self, app, test_user):
        """Test syncing user's group memberships"""
        with app.app_context():
            user = User.query.filter_by(email="test@example.com").first()
            user.google_groups_trends_earth_users = True
            user.google_groups_trendsearth = False

            service = GoogleGroupsService()

            # Mock successful service
            mock_service = Mock()
            mock_service.members().insert().execute.return_value = {"id": "member_123"}
            mock_service.members().delete().execute.return_value = {}
            service.service = mock_service

            result = service.sync_user_groups(user)

            assert result["user_email"] == "test@example.com"
            assert "groups" in result
            assert "trends_earth_users" in result["groups"]
            assert "trendsearth" in result["groups"]

    def test_get_google_groups_preferences_endpoint(self, app, test_user):
        """Test GET /user/me/google-groups endpoint"""
        with app.test_client() as client, app.app_context():
            user = User.query.filter_by(email="test@example.com").first()
            token = user.get_token()

            response = client.get(
                "/api/v1/user/me/google-groups",
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 200
            data = json.loads(response.data)
            assert "user_preferences" in data["data"]
            assert "available_groups" in data["data"]

    def test_update_google_groups_preferences_endpoint(self, app, test_user):
        """Test PUT /user/me/google-groups endpoint"""
        with app.test_client() as client, app.app_context():
            user = User.query.filter_by(email="test@example.com").first()
            token = user.get_token()

            # Mock Google Groups service
            with patch(
                "gefapi.routes.api.v1.google_groups.google_groups_service"
            ) as mock_service:
                mock_service.is_available.return_value = True
                mock_service.sync_user_groups.return_value = {
                    "user_email": "test@example.com",
                    "groups": {"trends_earth_users": {"success": True}},
                }

                response = client.put(
                    "/api/v1/user/me/google-groups",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "preferences": {
                            "trends_earth_users": True,
                            "trendsearth": False,
                        }
                    },
                )

                assert response.status_code == 200
                data = json.loads(response.data)
                assert (
                    data["data"]["message"]
                    == "Google Groups preferences updated successfully"
                )
                assert data["data"]["preferences"]["trends_earth_users"] is True

    def test_update_google_groups_invalid_preferences(self, app, test_user):
        """Test updating with invalid preferences"""
        with app.test_client() as client, app.app_context():
            user = User.query.filter_by(email="test@example.com").first()
            token = user.get_token()

            response = client.put(
                "/api/v1/user/me/google-groups",
                headers={"Authorization": f"Bearer {token}"},
                json={"preferences": {"invalid_group": True}},
            )

            assert response.status_code == 400
            data = json.loads(response.data)
            assert "Invalid group keys" in data["detail"]

    def test_sync_google_groups_endpoint(self, app, test_user):
        """Test POST /user/me/google-groups/sync endpoint"""
        with app.test_client() as client, app.app_context():
            user = User.query.filter_by(email="test@example.com").first()
            token = user.get_token()

            # Mock Google Groups service
            with patch(
                "gefapi.routes.api.v1.google_groups.google_groups_service"
            ) as mock_service:
                mock_service.is_available.return_value = True
                mock_service.sync_user_groups.return_value = {
                    "user_email": "test@example.com",
                    "groups": {"trends_earth_users": {"success": True}},
                }

                response = client.post(
                    "/api/v1/user/me/google-groups/sync",
                    headers={"Authorization": f"Bearer {token}"},
                )

                assert response.status_code == 200
                data = json.loads(response.data)
                assert data["data"]["message"] == "Google Groups sync completed"

    def test_google_groups_info_endpoint(self, app):
        """Test GET /google-groups/info endpoint (public)"""
        with app.test_client() as client:
            response = client.get("/api/v1/google-groups/info")

            assert response.status_code == 200
            data = json.loads(response.data)
            assert "available_groups" in data["data"]
            assert "trends_earth_users" in data["data"]["available_groups"]
            assert "trendsearth" in data["data"]["available_groups"]

    def test_sync_service_unavailable(self, app, test_user):
        """Test sync when Google Groups service is unavailable"""
        with app.test_client() as client, app.app_context():
            user = User.query.filter_by(email="test@example.com").first()
            token = user.get_token()

            # Mock unavailable service
            with patch(
                "gefapi.routes.api.v1.google_groups.google_groups_service"
            ) as mock_service:
                mock_service.is_available.return_value = False

                response = client.post(
                    "/api/v1/user/me/google-groups/sync",
                    headers={"Authorization": f"Bearer {token}"},
                )

                assert response.status_code == 503
                data = json.loads(response.data)
                assert "not available" in data["detail"]
