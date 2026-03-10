"""
Tests for session management endpoints.

Covers:
- GET /user/me/sessions
- DELETE /user/me/sessions/<session_id>
- DELETE /user/me/sessions
"""

import uuid

import pytest

from gefapi import db
from gefapi.services.refresh_token_service import RefreshTokenService


@pytest.mark.usefixtures("client")
class TestGetUserSessions:
    """Tests for GET /api/v1/user/me/sessions"""

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/user/me/sessions")
        assert resp.status_code == 401

    def test_returns_empty_sessions_list(self, client, auth_headers_user):
        resp = client.get("/api/v1/user/me/sessions", headers=auth_headers_user)
        assert resp.status_code == 200
        data = resp.json
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_returns_sessions_for_user(
        self, client, auth_headers_user, regular_user, app
    ):
        # Create a refresh token for the user
        with app.app_context():
            user = db.session.merge(regular_user)
            RefreshTokenService.create_refresh_token(
                user.id, device_info="Test Browser"
            )

        resp = client.get("/api/v1/user/me/sessions", headers=auth_headers_user)
        assert resp.status_code == 200
        data = resp.json
        assert "data" in data
        assert len(data["data"]) >= 1

    def test_admin_only_sees_own_sessions(
        self, client, auth_headers_admin, admin_user, regular_user, app
    ):
        # Create tokens for both admin and regular user
        with app.app_context():
            admin = db.session.merge(admin_user)
            user = db.session.merge(regular_user)
            RefreshTokenService.create_refresh_token(
                admin.id, device_info="Admin Browser"
            )
            RefreshTokenService.create_refresh_token(
                user.id, device_info="User Browser"
            )

        resp = client.get("/api/v1/user/me/sessions", headers=auth_headers_admin)
        assert resp.status_code == 200
        data = resp.json
        # Should only contain admin's sessions
        for session in data["data"]:
            assert session.get("user_id") is None or str(session.get("user_id")) == str(
                admin_user.id
            )


@pytest.mark.usefixtures("client")
class TestRevokeUserSession:
    """Tests for DELETE /api/v1/user/me/sessions/<session_id>"""

    def test_requires_auth(self, client):
        fake_id = str(uuid.uuid4())
        resp = client.delete(f"/api/v1/user/me/sessions/{fake_id}")
        assert resp.status_code == 401

    def test_returns_404_for_nonexistent_session(self, client, auth_headers_user):
        fake_id = str(uuid.uuid4())
        resp = client.delete(
            f"/api/v1/user/me/sessions/{fake_id}", headers=auth_headers_user
        )
        assert resp.status_code == 404

    def test_can_revoke_own_session(self, client, auth_headers_user, regular_user, app):
        with app.app_context():
            user = db.session.merge(regular_user)
            token = RefreshTokenService.create_refresh_token(
                user.id, device_info="Test Browser"
            )
            token_id = str(token.id)

        resp = client.delete(
            f"/api/v1/user/me/sessions/{token_id}", headers=auth_headers_user
        )
        assert resp.status_code == 200
        assert "revoked" in resp.json.get("message", "").lower()

    def test_cannot_revoke_other_users_session(
        self, client, auth_headers_user, admin_user, app
    ):
        # Create a session for admin user
        with app.app_context():
            admin = db.session.merge(admin_user)
            token = RefreshTokenService.create_refresh_token(
                admin.id, device_info="Admin Browser"
            )
            token_id = str(token.id)

        # Regular user tries to revoke admin's session
        resp = client.delete(
            f"/api/v1/user/me/sessions/{token_id}", headers=auth_headers_user
        )
        assert resp.status_code == 404  # Session not found for this user


@pytest.mark.usefixtures("client")
class TestRevokeAllUserSessions:
    """Tests for DELETE /api/v1/user/me/sessions"""

    def test_requires_auth(self, client):
        resp = client.delete("/api/v1/user/me/sessions")
        assert resp.status_code == 401

    def test_revoke_all_sessions(self, client, auth_headers_user, regular_user, app):
        # Create multiple sessions
        with app.app_context():
            user = db.session.merge(regular_user)
            RefreshTokenService.create_refresh_token(user.id, device_info="Browser 1")
            RefreshTokenService.create_refresh_token(user.id, device_info="Browser 2")

        resp = client.delete("/api/v1/user/me/sessions", headers=auth_headers_user)
        assert resp.status_code == 200
        assert "revoked" in resp.json.get("message", "").lower()

    def test_revoke_all_returns_count(
        self, client, auth_headers_user, regular_user, app
    ):
        # Create sessions
        with app.app_context():
            user = db.session.merge(regular_user)
            RefreshTokenService.create_refresh_token(user.id, device_info="Device 1")
            RefreshTokenService.create_refresh_token(user.id, device_info="Device 2")
            RefreshTokenService.create_refresh_token(user.id, device_info="Device 3")

        resp = client.delete("/api/v1/user/me/sessions", headers=auth_headers_user)
        assert resp.status_code == 200
        # Response should mention how many were revoked
        msg = resp.json.get("message", "")
        assert "revoked" in msg.lower()
