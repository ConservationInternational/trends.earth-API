"""Tests for openEO credential management.

Covers:
- User model helpers (set / get / has / clear / encrypt / serialize)
- OpenEOCredentialService URL validation and connect() logic
- All HTTP routes: GET / POST / DELETE / check
"""

import json
import os
from unittest.mock import MagicMock, Mock, patch
import uuid

import pytest

from gefapi import db
from gefapi.models.user import User
from gefapi.services.openeo_credential_service import OpenEOCredentialService

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-encryption")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(email=None, app=None):
    email = email or f"openeo-test-{uuid.uuid4().hex[:8]}@example.com"
    return User(
        email=email,
        password="password123",
        name="Test User",
        country="TC",
        institution="Test Inst",
    )


def _token_for(user, app):
    from flask_jwt_extended import create_access_token

    with app.app_context():
        return create_access_token(identity=user.id)


OIDC_CREDS = {
    "type": "oidc_refresh_token",
    "provider_id": "egi",
    "client_id": "trends-earth",
    "client_secret": "s3cr3t",
    "refresh_token": "rt-abc123",
}

BASIC_CREDS = {
    "type": "basic",
    "username": "user@example.com",
    "password": "hunter2",
}


# ===========================================================================
# User model helpers
# ===========================================================================


class TestUserOpenEOCredentialHelpers:
    """Unit tests for User openEO credential model methods."""

    def test_has_no_credentials_by_default(self, app):
        with app.app_context():
            user = _make_user()
            assert user.has_openeo_credentials() is False

    def test_set_and_has_credentials(self, app):
        with app.app_context():
            user = _make_user()
            user.set_openeo_credentials(OIDC_CREDS)
            assert user.has_openeo_credentials() is True

    def test_get_returns_stored_credentials(self, app):
        with app.app_context():
            user = _make_user()
            user.set_openeo_credentials(OIDC_CREDS)
            retrieved = user.get_openeo_credentials()
            assert retrieved == OIDC_CREDS

    def test_get_basic_credentials(self, app):
        with app.app_context():
            user = _make_user()
            user.set_openeo_credentials(BASIC_CREDS)
            retrieved = user.get_openeo_credentials()
            assert retrieved == BASIC_CREDS

    def test_clear_removes_credentials(self, app):
        with app.app_context():
            user = _make_user()
            user.set_openeo_credentials(OIDC_CREDS)
            assert user.has_openeo_credentials()
            user.clear_openeo_credentials()
            assert not user.has_openeo_credentials()
            assert user.openeo_credentials_enc is None

    def test_credentials_are_encrypted_at_rest(self, app):
        """The raw column value must not contain plaintext secrets."""
        with app.app_context():
            user = _make_user()
            user.set_openeo_credentials(OIDC_CREDS)
            raw = user.openeo_credentials_enc
            # The stored value must not contain the plaintext refresh token
            assert "rt-abc123" not in raw
            assert "s3cr3t" not in raw

    def test_get_returns_none_when_no_credentials(self, app):
        with app.app_context():
            user = _make_user()
            assert user.get_openeo_credentials() is None

    def test_serialization_includes_openeo_credentials_flag(self, app):
        with app.app_context():
            user = _make_user()
            db.session.add(user)
            db.session.commit()

            # Without credentials
            data = user.serialize(include=["openeo_credentials"])
            assert "openeo_credentials" in data
            assert data["openeo_credentials"]["has_credentials"] is False

            # With credentials
            user.set_openeo_credentials(OIDC_CREDS)
            data = user.serialize(include=["openeo_credentials"])
            assert data["openeo_credentials"]["has_credentials"] is True

    def test_serialization_does_not_expose_secrets(self, app):
        with app.app_context():
            user = _make_user()
            db.session.add(user)
            db.session.commit()
            user.set_openeo_credentials(OIDC_CREDS)
            data = user.serialize(include=["openeo_credentials"])
            serialized = json.dumps(data)
            assert "rt-abc123" not in serialized
            assert "s3cr3t" not in serialized
            assert "hunter2" not in serialized


# ===========================================================================
# OpenEOCredentialService – URL validation
# ===========================================================================


class TestOpenEOCredentialServiceURLValidation:
    """_validate_backend_url() must enforce https://."""

    def test_valid_https_url_accepted(self):
        url = "https://openeo.example.com"
        assert OpenEOCredentialService._validate_backend_url(url) == url

    def test_http_url_rejected(self):
        with pytest.raises(ValueError, match="https://"):
            OpenEOCredentialService._validate_backend_url("http://openeo.example.com")

    def test_ftp_url_rejected(self):
        with pytest.raises(ValueError):
            OpenEOCredentialService._validate_backend_url("ftp://openeo.example.com")

    def test_url_without_host_rejected(self):
        with pytest.raises(ValueError, match="no host"):
            OpenEOCredentialService._validate_backend_url("https://")


# ===========================================================================
# OpenEOCredentialService – _resolve_backend_url
# ===========================================================================


class TestOpenEOCredentialServiceResolveBackendURL:
    """_resolve_backend_url() should pick the right source in priority order."""

    def test_script_url_takes_highest_priority(self):
        script = Mock(openeo_backend_url="https://script.openeo.example.com")
        url = OpenEOCredentialService._resolve_backend_url(script=script)
        assert url == "https://script.openeo.example.com"

    def test_environment_dict_takes_second_priority(self):
        script = Mock(openeo_backend_url=None)
        env = {"OPENEO_BACKEND_URL": "https://env.openeo.example.com"}
        url = OpenEOCredentialService._resolve_backend_url(script=script, environment=env)
        assert url == "https://env.openeo.example.com"

    def test_settings_default_used_when_no_override(self):
        script = Mock(openeo_backend_url=None)
        with patch(
            "gefapi.services.openeo_credential_service.SETTINGS",
            {"OPENEO_DEFAULT_BACKEND_URL": "https://default.openeo.example.com"},
        ):
            url = OpenEOCredentialService._resolve_backend_url(script=script)
        assert url == "https://default.openeo.example.com"

    def test_raises_when_nothing_configured(self):
        script = Mock(openeo_backend_url=None)
        with patch(
            "gefapi.services.openeo_credential_service.SETTINGS", {}
        ):
            with pytest.raises(ValueError, match="No openEO backend URL"):
                OpenEOCredentialService._resolve_backend_url(script=script)

    def test_script_http_url_rejected(self):
        script = Mock(openeo_backend_url="http://insecure.openeo.example.com")
        with pytest.raises(ValueError, match="https://"):
            OpenEOCredentialService._resolve_backend_url(script=script)


# ===========================================================================
# OpenEOCredentialService – connect()
# ===========================================================================


class TestOpenEOCredentialServiceConnect:
    """connect() should authenticate in the correct priority order."""

    def _make_mock_conn(self):
        conn = MagicMock()
        return conn

    @patch("gefapi.services.openeo_credential_service.openeo")
    def test_user_oidc_credentials_used_first(self, mock_openeo, app):
        conn = self._make_mock_conn()
        mock_openeo.connect.return_value = conn

        with app.app_context():
            user = _make_user()
            user.set_openeo_credentials(OIDC_CREDS)

            with patch(
                "gefapi.services.openeo_credential_service.SETTINGS",
                {"OPENEO_DEFAULT_BACKEND_URL": "https://openeo.example.com"},
            ):
                result = OpenEOCredentialService.connect(user=user)

        conn.authenticate_oidc_refresh_token.assert_called_once()
        call_kwargs = conn.authenticate_oidc_refresh_token.call_args.kwargs
        assert call_kwargs["refresh_token"] == "rt-abc123"
        assert call_kwargs["client_id"] == "trends-earth"

    @patch("gefapi.services.openeo_credential_service.openeo")
    def test_user_basic_credentials_used(self, mock_openeo, app):
        conn = self._make_mock_conn()
        mock_openeo.connect.return_value = conn

        with app.app_context():
            user = _make_user()
            user.set_openeo_credentials(BASIC_CREDS)

            with patch(
                "gefapi.services.openeo_credential_service.SETTINGS",
                {"OPENEO_DEFAULT_BACKEND_URL": "https://openeo.example.com"},
            ):
                result = OpenEOCredentialService.connect(user=user)

        conn.authenticate_basic.assert_called_once_with("user@example.com", "hunter2")

    @patch("gefapi.services.openeo_credential_service.openeo")
    def test_system_oidc_fallback_when_no_user_creds(self, mock_openeo, app):
        conn = self._make_mock_conn()
        mock_openeo.connect.return_value = conn

        env_settings = {
            "OPENEO_REFRESH_TOKEN": "sys-refresh-token",
            "OPENEO_CLIENT_ID": "sys-client",
            "OPENEO_CLIENT_SECRET": "sys-secret",
            "OPENEO_PROVIDER_ID": "egi",
        }

        with app.app_context():
            user = _make_user()  # no creds

            with patch(
                "gefapi.services.openeo_credential_service.SETTINGS",
                {
                    "OPENEO_DEFAULT_BACKEND_URL": "https://openeo.example.com",
                    "environment": env_settings,
                },
            ):
                result = OpenEOCredentialService.connect(user=user)

        conn.authenticate_oidc_refresh_token.assert_called_once()
        kw = conn.authenticate_oidc_refresh_token.call_args.kwargs
        assert kw["refresh_token"] == "sys-refresh-token"

    @patch("gefapi.services.openeo_credential_service.openeo")
    def test_system_basic_fallback_when_no_oidc(self, mock_openeo, app):
        conn = self._make_mock_conn()
        mock_openeo.connect.return_value = conn

        env_settings = {
            "OPENEO_USERNAME": "sysuser",
            "OPENEO_PASSWORD": "syspass",
        }

        with app.app_context():
            user = _make_user()  # no creds

            with patch(
                "gefapi.services.openeo_credential_service.SETTINGS",
                {
                    "OPENEO_DEFAULT_BACKEND_URL": "https://openeo.example.com",
                    "environment": env_settings,
                },
            ):
                result = OpenEOCredentialService.connect(user=user)

        conn.authenticate_basic.assert_called_once_with("sysuser", "syspass")

    @patch("gefapi.services.openeo_credential_service.openeo")
    def test_anonymous_when_no_credentials_anywhere(self, mock_openeo, app):
        conn = self._make_mock_conn()
        mock_openeo.connect.return_value = conn

        with app.app_context():
            user = _make_user()  # no creds

            with patch(
                "gefapi.services.openeo_credential_service.SETTINGS",
                {
                    "OPENEO_DEFAULT_BACKEND_URL": "https://openeo.example.com",
                    "environment": {},
                },
            ):
                result = OpenEOCredentialService.connect(user=user)

        conn.authenticate_oidc_refresh_token.assert_not_called()
        conn.authenticate_basic.assert_not_called()

    @patch("gefapi.services.openeo_credential_service.openeo")
    def test_user_cred_failure_falls_through_to_system(self, mock_openeo, app):
        """If per-user auth throws, fall through to system credentials."""
        conn = self._make_mock_conn()
        conn.authenticate_oidc_refresh_token.side_effect = [
            Exception("user auth failed"),  # first call (user creds)
            None,  # second call (system creds)
        ]
        mock_openeo.connect.return_value = conn

        env_settings = {
            "OPENEO_REFRESH_TOKEN": "sys-rt",
            "OPENEO_CLIENT_ID": "sys",
            "OPENEO_CLIENT_SECRET": "sec",
        }

        with app.app_context():
            user = _make_user()
            user.set_openeo_credentials(OIDC_CREDS)

            with patch(
                "gefapi.services.openeo_credential_service.SETTINGS",
                {
                    "OPENEO_DEFAULT_BACKEND_URL": "https://openeo.example.com",
                    "environment": env_settings,
                },
            ):
                result = OpenEOCredentialService.connect(user=user)

        assert conn.authenticate_oidc_refresh_token.call_count == 2

    @patch("gefapi.services.openeo_credential_service.openeo", None)
    def test_raises_import_error_when_openeo_missing(self, app):
        with app.app_context():
            user = _make_user()
            with pytest.raises(ImportError, match="openeo"):
                OpenEOCredentialService.connect(user=user)


# ===========================================================================
# HTTP routes – GET /user/me/openeo-credentials
# ===========================================================================


class TestGetOpenEOCredentialsRoute:
    def _auth_header(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_returns_no_credentials_when_none_stored(self, app, client, regular_user):
        with app.app_context():
            from flask_jwt_extended import create_access_token

            user = User.query.filter_by(email=regular_user.email).first()
            token = create_access_token(identity=user.id)

        resp = client.get(
            "/api/v1/user/me/openeo-credentials",
            headers=self._auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["has_credentials"] is False
        assert data["credential_type"] is None

    def test_returns_credential_type_when_stored(self, app, client, regular_user):
        with app.app_context():
            from flask_jwt_extended import create_access_token

            user = User.query.filter_by(email=regular_user.email).first()
            user.set_openeo_credentials(OIDC_CREDS)
            db.session.commit()
            token = create_access_token(identity=user.id)

        resp = client.get(
            "/api/v1/user/me/openeo-credentials",
            headers=self._auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["has_credentials"] is True
        assert data["credential_type"] == "oidc_refresh_token"

    def test_requires_authentication(self, client):
        resp = client.get("/api/v1/user/me/openeo-credentials")
        assert resp.status_code == 401


# ===========================================================================
# HTTP routes – POST /user/me/openeo-credentials
# ===========================================================================


class TestPostOpenEOCredentialsRoute:
    def _auth_header(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _token(self, app, user):
        with app.app_context():
            from flask_jwt_extended import create_access_token

            u = User.query.filter_by(email=user.email).first()
            return create_access_token(identity=u.id)

    def test_store_oidc_credentials(self, app, client, regular_user):
        token = self._token(app, regular_user)
        resp = client.post(
            "/api/v1/user/me/openeo-credentials",
            headers=self._auth_header(token),
            json=OIDC_CREDS,
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["status"] == "credentials stored"

    def test_store_basic_credentials(self, app, client, regular_user):
        token = self._token(app, regular_user)
        resp = client.post(
            "/api/v1/user/me/openeo-credentials",
            headers=self._auth_header(token),
            json=BASIC_CREDS,
        )
        assert resp.status_code == 200

    def test_invalid_credential_type_rejected(self, app, client, regular_user):
        token = self._token(app, regular_user)
        resp = client.post(
            "/api/v1/user/me/openeo-credentials",
            headers=self._auth_header(token),
            json={"type": "kerberos", "ticket": "xyz"},
        )
        assert resp.status_code == 400
        assert "Invalid credential type" in resp.get_json()["detail"]

    def test_missing_required_oidc_fields_rejected(self, app, client, regular_user):
        token = self._token(app, regular_user)
        resp = client.post(
            "/api/v1/user/me/openeo-credentials",
            headers=self._auth_header(token),
            json={"type": "oidc_refresh_token", "provider_id": "egi"},
            # missing client_id and refresh_token
        )
        assert resp.status_code == 400
        detail = resp.get_json()["detail"]
        assert "client_id" in detail or "refresh_token" in detail

    def test_missing_required_basic_fields_rejected(self, app, client, regular_user):
        token = self._token(app, regular_user)
        resp = client.post(
            "/api/v1/user/me/openeo-credentials",
            headers=self._auth_header(token),
            json={"type": "basic", "username": "user@example.com"},
            # missing password
        )
        assert resp.status_code == 400

    def test_no_json_body_rejected(self, app, client, regular_user):
        token = self._token(app, regular_user)
        resp = client.post(
            "/api/v1/user/me/openeo-credentials",
            headers=self._auth_header(token),
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    def test_extra_fields_are_stripped(self, app, client, regular_user):
        """Only whitelisted fields should be persisted."""
        token = self._token(app, regular_user)
        payload = {
            **OIDC_CREDS,
            "injected_field": "malicious",
            "arbitrary_key": "value",
        }
        resp = client.post(
            "/api/v1/user/me/openeo-credentials",
            headers=self._auth_header(token),
            json=payload,
        )
        assert resp.status_code == 200

        with app.app_context():
            user = User.query.filter_by(email=regular_user.email).first()
            stored = user.get_openeo_credentials()
            assert "injected_field" not in stored
            assert "arbitrary_key" not in stored

    def test_requires_authentication(self, client):
        resp = client.post("/api/v1/user/me/openeo-credentials", json=OIDC_CREDS)
        assert resp.status_code == 401


# ===========================================================================
# HTTP routes – DELETE /user/me/openeo-credentials
# ===========================================================================


class TestDeleteOpenEOCredentialsRoute:
    def _auth_header(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _token(self, app, user):
        with app.app_context():
            from flask_jwt_extended import create_access_token

            u = User.query.filter_by(email=user.email).first()
            return create_access_token(identity=u.id)

    def test_deletes_existing_credentials(self, app, client, regular_user):
        # Store creds first
        with app.app_context():
            user = User.query.filter_by(email=regular_user.email).first()
            user.set_openeo_credentials(OIDC_CREDS)
            db.session.commit()

        token = self._token(app, regular_user)
        resp = client.delete(
            "/api/v1/user/me/openeo-credentials",
            headers=self._auth_header(token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["status"] == "credentials removed"

        # Verify they are gone
        with app.app_context():
            user = User.query.filter_by(email=regular_user.email).first()
            assert not user.has_openeo_credentials()

    def test_delete_when_none_stored_returns_ok(self, app, client, regular_user):
        """DELETE is idempotent – should succeed even if no creds are stored."""
        token = self._token(app, regular_user)
        resp = client.delete(
            "/api/v1/user/me/openeo-credentials",
            headers=self._auth_header(token),
        )
        assert resp.status_code == 200

    def test_requires_authentication(self, client):
        resp = client.delete("/api/v1/user/me/openeo-credentials")
        assert resp.status_code == 401


# ===========================================================================
# HTTP routes – GET /user/me/openeo-credentials/check
# ===========================================================================


class TestCheckOpenEOCredentialsRoute:
    def _auth_header(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _token(self, app, user):
        with app.app_context():
            from flask_jwt_extended import create_access_token

            u = User.query.filter_by(email=user.email).first()
            return create_access_token(identity=u.id)

    def test_check_returns_false_when_no_credentials(self, app, client, regular_user):
        token = self._token(app, regular_user)
        resp = client.get(
            "/api/v1/user/me/openeo-credentials/check",
            headers=self._auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["valid"] is False
        assert "No credentials" in data["message"]

    @patch("gefapi.routes.api.v1.openeo_credentials.OpenEOCredentialService.validate_credentials")
    def test_check_returns_valid_when_credentials_work(
        self, mock_validate, app, client, regular_user
    ):
        mock_validate.return_value = True

        with app.app_context():
            user = User.query.filter_by(email=regular_user.email).first()
            user.set_openeo_credentials(OIDC_CREDS)
            db.session.commit()

        token = self._token(app, regular_user)
        resp = client.get(
            "/api/v1/user/me/openeo-credentials/check",
            headers=self._auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["valid"] is True

    @patch("gefapi.routes.api.v1.openeo_credentials.OpenEOCredentialService.validate_credentials")
    def test_check_returns_invalid_when_credentials_fail(
        self, mock_validate, app, client, regular_user
    ):
        mock_validate.return_value = False

        with app.app_context():
            user = User.query.filter_by(email=regular_user.email).first()
            user.set_openeo_credentials(OIDC_CREDS)
            db.session.commit()

        token = self._token(app, regular_user)
        resp = client.get(
            "/api/v1/user/me/openeo-credentials/check",
            headers=self._auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["valid"] is False
        assert "invalid" in data["message"].lower() or "unreachable" in data["message"].lower()

    def test_requires_authentication(self, client):
        resp = client.get("/api/v1/user/me/openeo-credentials/check")
        assert resp.status_code == 401
