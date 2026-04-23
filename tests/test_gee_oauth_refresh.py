"""Tests for the GEE OAuth token-refresh fix.

Specifically covers the change from ``credentials.refresh(None)`` to
``credentials.refresh(Request())`` in
:meth:`gefapi.services.gee_service.GEEService._initialize_ee_with_oauth`.
"""

import os
from unittest.mock import MagicMock, patch
import uuid

from gefapi.models.user import User
from gefapi.services.gee_service import GEEService

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-encryption")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key")


def _make_user(email=None):
    email = email or f"test-{uuid.uuid4().hex[:8]}@example.com"
    return User(
        email=email,
        password="password123",
        name="Test User",
        country="TC",
        institution="Test Inst",
    )


# ---------------------------------------------------------------------------
# _oauth_token_uri
# ---------------------------------------------------------------------------


class TestOAuthTokenUri:
    """_oauth_token_uri() should return a validated URL."""

    def test_returns_default_when_not_configured(self):
        """When GOOGLE_OAUTH_TOKEN_URI is absent, the default is returned."""
        with patch("gefapi.services.gee_service.SETTINGS", {"environment": {}}):
            uri = GEEService._oauth_token_uri()
        assert uri == "https://oauth2.googleapis.com/token"

    def test_returns_configured_uri(self):
        """A valid configured URI is returned unchanged."""
        custom_uri = "https://custom.oauth.example.com/token"
        with patch(
            "gefapi.services.gee_service.SETTINGS",
            {"environment": {"GOOGLE_OAUTH_TOKEN_URI": custom_uri}},
        ):
            uri = GEEService._oauth_token_uri()
        assert uri == custom_uri

    def test_falls_back_to_default_for_invalid_uri(self):
        """An invalid (no scheme/host) URI falls back to the default."""
        with patch(
            "gefapi.services.gee_service.SETTINGS",
            {"environment": {"GOOGLE_OAUTH_TOKEN_URI": "not-a-url"}},
        ):
            uri = GEEService._oauth_token_uri()
        assert uri == "https://oauth2.googleapis.com/token"

    def test_env_var_takes_precedence_when_settings_empty(self):
        """GOOGLE_OAUTH_TOKEN_URI env var is used when settings has no entry."""
        custom = "https://env.example.com/token"
        with (
            patch("gefapi.services.gee_service.SETTINGS", {"environment": {}}),
            patch.dict(os.environ, {"GOOGLE_OAUTH_TOKEN_URI": custom}),
        ):
            uri = GEEService._oauth_token_uri()
        assert uri == custom


# ---------------------------------------------------------------------------
# _initialize_ee_with_oauth – token not expired
# ---------------------------------------------------------------------------


class TestInitializeEEWithOAuthNotExpired:
    """When the OAuth token is still valid, no refresh should occur."""

    @patch("gefapi.services.gee_service.ee")
    def test_no_refresh_when_token_valid(self, mock_ee, app):
        """EE should be initialized without calling refresh when token is fresh."""
        with app.app_context():
            user = _make_user()
            user.set_gee_oauth_credentials("valid_token", "refresh_token", cloud_project="test-project")

            mock_creds = MagicMock()
            mock_creds.expired = False
            mock_creds.refresh_token = "refresh_token"

            with patch(
                "gefapi.services.gee_service.Credentials", return_value=mock_creds
            ):
                result = GEEService._initialize_ee_with_oauth(user)

            assert result is True
            mock_creds.refresh.assert_not_called()
            mock_ee.Initialize.assert_called_once_with(mock_creds, project="test-project")

    @patch("gefapi.services.gee_service.ee")
    def test_succeeds_even_when_ee_already_initialized(self, mock_ee, app):
        """Initialization should succeed even if the EE check raises."""
        with app.app_context():
            user = _make_user()
            user.set_gee_oauth_credentials("tok", "ref", cloud_project="test-project")

            mock_creds = MagicMock()
            mock_creds.expired = False

            with patch(
                "gefapi.services.gee_service.Credentials", return_value=mock_creds
            ):
                result = GEEService._initialize_ee_with_oauth(user)

            assert result is True


# ---------------------------------------------------------------------------
# _initialize_ee_with_oauth – token expired (the key fix under test)
# ---------------------------------------------------------------------------


class TestInitializeEEWithOAuthExpiredToken:
    """When the token is expired, refresh() must be called with a Request object."""

    @patch("gefapi.services.gee_service.ee")
    def test_refresh_called_with_request_object(self, mock_ee, app):
        """refresh() must receive a google.auth.transport.requests.Request instance."""
        with app.app_context():
            user = _make_user()
            user.set_gee_oauth_credentials("old_token", "refresh_token", cloud_project="test-project")

            # Simulate an expired token
            mock_creds = MagicMock()
            mock_creds.expired = True
            mock_creds.refresh_token = "refresh_token"
            # After refresh, updated token is available
            mock_creds.token = "new_token"

            mock_request_cls = MagicMock()
            mock_request_instance = MagicMock()
            mock_request_cls.return_value = mock_request_instance

            with (
                patch(
                    "gefapi.services.gee_service.Credentials", return_value=mock_creds
                ),
                patch(
                    "gefapi.services.gee_service.GEEService._oauth_token_uri",
                    return_value="https://oauth2.googleapis.com/token",
                ),
                patch("google.auth.transport.requests.Request", mock_request_cls),
            ):
                result = GEEService._initialize_ee_with_oauth(user)

            assert result is True
            # Verify refresh was called with a Request() instance, not None
            mock_creds.refresh.assert_called_once_with(mock_request_instance)

    @patch("gefapi.services.gee_service.ee")
    def test_stored_token_updated_after_refresh(self, mock_ee, app):
        """Updated tokens should be persisted to the user after a successful refresh."""
        from gefapi import db

        with app.app_context():
            user = _make_user()
            user.set_gee_oauth_credentials("old_tok", "old_refresh", cloud_project="test-project")
            db.session.add(user)
            db.session.commit()

            mock_creds = MagicMock()
            mock_creds.expired = True
            mock_creds.refresh_token = "new_refresh"
            mock_creds.token = "new_tok"

            with (
                patch(
                    "gefapi.services.gee_service.Credentials", return_value=mock_creds
                ),
                patch(
                    "google.auth.transport.requests.Request",
                    return_value=MagicMock(),
                ),
            ):
                result = GEEService._initialize_ee_with_oauth(user)

            assert result is True
            # Credentials should have been updated in the DB
            access, refresh, _ = user.get_gee_oauth_credentials()
            assert access == "new_tok"
            assert refresh == "new_refresh"

    @patch("gefapi.services.gee_service.ee")
    def test_refresh_error_returns_false(self, mock_ee, app):
        """A RefreshError during token refresh should cause the method to return False."""
        from google.auth.exceptions import RefreshError

        with app.app_context():
            user = _make_user()
            user.set_gee_oauth_credentials("old_tok", "old_refresh")

            mock_creds = MagicMock()
            mock_creds.expired = True
            mock_creds.refresh_token = "old_refresh"
            mock_creds.refresh.side_effect = RefreshError("token expired")

            with (
                patch(
                    "gefapi.services.gee_service.Credentials", return_value=mock_creds
                ),
                patch(
                    "google.auth.transport.requests.Request",
                    return_value=MagicMock(),
                ),
            ):
                result = GEEService._initialize_ee_with_oauth(user)

            assert result is False
            mock_ee.Initialize.assert_not_called()


# ---------------------------------------------------------------------------
# _initialize_ee_with_oauth – missing tokens
# ---------------------------------------------------------------------------


class TestInitializeEEWithOAuthMissingTokens:
    """Missing access / refresh tokens should cause early failure."""

    @patch("gefapi.services.gee_service.ee")
    def test_missing_tokens_returns_false(self, mock_ee, app):
        """If the user has no tokens stored, initialisation must fail."""
        with app.app_context():
            user = _make_user()
            # Manually set to empty strings to simulate missing tokens
            user.gee_oauth_token = None
            user.gee_refresh_token = None
            user.gee_credentials_type = "oauth"

            result = GEEService._initialize_ee_with_oauth(user)

        assert result is False
        mock_ee.Initialize.assert_not_called()


# ---------------------------------------------------------------------------
# _initialize_ee dispatch path for OAuth users
# ---------------------------------------------------------------------------


class TestInitializeEEDispatch:
    """_initialize_ee should route to OAuth path for oauth credential type."""

    @patch("gefapi.services.gee_service.GEEService._initialize_ee_with_oauth")
    @patch("gefapi.services.gee_service.ee")
    def test_dispatches_to_oauth_when_credential_type_is_oauth(
        self, mock_ee, mock_oauth_init, app
    ):
        """For a user with gee_credentials_type == 'oauth', the OAuth path is used."""
        with app.app_context():
            user = _make_user()
            user.set_gee_oauth_credentials("tok", "ref")

            # Make EE check raise so we don't short-circuit
            mock_ee.data.listOperations.side_effect = Exception("not init")
            mock_oauth_init.return_value = True

            result = GEEService._initialize_ee(user)

        assert result is True
        mock_oauth_init.assert_called_once_with(user)

    @patch(
        "gefapi.services.gee_service.GEEService._initialize_ee_with_user_service_account"
    )
    @patch("gefapi.services.gee_service.ee")
    def test_dispatches_to_service_account_when_credential_type_matches(
        self, mock_ee, mock_sa_init, app
    ):
        """For service_account credential type, the SA path is used."""
        with app.app_context():
            user = _make_user()
            user.gee_credentials_type = "service_account"
            user.gee_service_account_key = user._encrypt_gee_data(
                '{"client_email": "test@sa.com"}'
            )

            mock_ee.data.listOperations.side_effect = Exception("not init")
            mock_sa_init.return_value = True

            result = GEEService._initialize_ee(user)

        assert result is True
        mock_sa_init.assert_called_once_with(user)
