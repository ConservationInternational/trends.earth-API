"""Tests for security audit fixes: H6, C1, C2, and M1.

H6: S3 object_basename path traversal sanitization
C1: GEE encryption key derivation via HKDF
C2: Token blocklist fail-closed behaviour in production
M1: Generic password recovery response (no user enumeration)
"""

import base64
import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# H6 — S3 object_basename sanitization
# ---------------------------------------------------------------------------


class TestS3ObjectBasenameSanitization:
    """H6: Verify _sanitize_object_basename prevents path traversal."""

    def test_clean_basename_passes_through(self):
        from gefapi.s3 import _sanitize_object_basename

        assert _sanitize_object_basename("script.tar.gz") == "script.tar.gz"
        assert _sanitize_object_basename("my_file.py") == "my_file.py"

    def test_rejects_empty_basename(self):
        from gefapi.s3 import _sanitize_object_basename

        with pytest.raises(ValueError, match="must not be empty"):
            _sanitize_object_basename("")

    def test_rejects_dot_dot_traversal(self):
        from gefapi.s3 import _sanitize_object_basename

        with pytest.raises(ValueError, match="path traversal"):
            _sanitize_object_basename("../../etc/passwd")

    def test_rejects_encoded_dot_dot(self):
        from gefapi.s3 import _sanitize_object_basename

        with pytest.raises(ValueError, match="path traversal"):
            _sanitize_object_basename("..%2F..%2Fsecret")

    def test_strips_directory_prefix(self):
        from gefapi.s3 import _sanitize_object_basename

        assert _sanitize_object_basename("some/dir/file.tar.gz") == "file.tar.gz"

    def test_strips_windows_backslash_prefix(self):
        from gefapi.s3 import _sanitize_object_basename

        assert _sanitize_object_basename("some\\dir\\file.tar.gz") == "file.tar.gz"

    def test_rejects_basename_resolving_to_empty(self):
        from gefapi.s3 import _sanitize_object_basename

        with pytest.raises(ValueError, match="resolved to empty"):
            _sanitize_object_basename("/")

    @pytest.fixture(autouse=True)
    def _disable_s3_mocks(self, mock_external_services, monkeypatch):
        """Re-import the *real* S3 functions so our tests exercise the
        actual sanitization path.  The autouse ``mock_external_services``
        fixture from conftest patches these with MagicMock objects; we undo
        that here by stopping the patches before the test body runs.

        Because ``mock_external_services`` is a context-manager fixture we
        cannot remove its patches directly.  Instead we simply re-bind the
        names in ``gefapi.s3`` back to the originals.
        """
        import gefapi.s3 as _s3_mod

        # The real implementations are still available in the module's
        # globals because ``unittest.mock.patch`` saves/restores them.
        # We grab them from the *source* via importlib reload.
        import importlib

        _real = importlib.reload(_s3_mod)
        monkeypatch.setattr("gefapi.s3.push_script_to_s3", _real.push_script_to_s3)
        monkeypatch.setattr("gefapi.s3.get_script_from_s3", _real.get_script_from_s3)
        monkeypatch.setattr("gefapi.s3.push_params_to_s3", _real.push_params_to_s3)

    def test_push_script_calls_sanitize(self):
        """Ensure push_script_to_s3 rejects traversal basenames."""
        from gefapi.s3 import push_script_to_s3

        with pytest.raises(ValueError, match="path traversal"):
            push_script_to_s3("/tmp/file", "../../etc/passwd")

    def test_get_script_calls_sanitize(self):
        """Ensure get_script_from_s3 rejects traversal basenames."""
        from gefapi.s3 import get_script_from_s3

        with pytest.raises(ValueError, match="path traversal"):
            get_script_from_s3("../../other_prefix/secret", "/tmp/out")

    def test_delete_script_calls_sanitize(self):
        """Ensure delete_script_from_s3 rejects traversal basenames."""
        from gefapi.s3 import delete_script_from_s3

        with pytest.raises(ValueError, match="path traversal"):
            delete_script_from_s3("../../other_prefix/secret")

    def test_push_params_calls_sanitize(self):
        """Ensure push_params_to_s3 rejects traversal basenames."""
        from gefapi.s3 import push_params_to_s3

        with pytest.raises(ValueError, match="path traversal"):
            push_params_to_s3("/tmp/file", "../../etc/passwd")


# ---------------------------------------------------------------------------
# C1 — HKDF-based GEE encryption key derivation
# ---------------------------------------------------------------------------


class TestGEEEncryptionKeyDerivation:
    """C1: Verify HKDF key derivation and legacy fallback decryption."""

    @staticmethod
    def _make_user_stub(email="test@example.com"):
        """Create a lightweight object with User's encryption methods.

        We cannot instantiate the SQLAlchemy-mapped ``User`` model outside
        a session without triggering instrumentation errors, so we build a
        plain object and bind the encryption helpers directly.
        """
        from gefapi.models.user import User

        class _Stub:
            pass

        stub = _Stub()
        stub.email = email
        # Bind instance methods
        stub._encrypt_gee_data = User._encrypt_gee_data.__get__(stub)
        stub._decrypt_gee_data = User._decrypt_gee_data.__get__(stub)
        # Bind static methods so self._get_encryption_key() calls work
        stub._get_encryption_key = User._get_encryption_key
        stub._get_legacy_encryption_key = User._get_legacy_encryption_key
        return stub

    def test_hkdf_key_is_valid_fernet_key(self, app):
        """The HKDF-derived key must be a valid Fernet key (32 bytes b64)."""
        from cryptography.fernet import Fernet

        from gefapi.models.user import User

        # Clear cached key from any previous test
        User._get_encryption_key.cache_clear()
        with app.app_context():
            key = User._get_encryption_key()
            # Should not raise
            Fernet(key)
            # 44 bytes of URL-safe base64 = 32 raw bytes
            assert len(base64.urlsafe_b64decode(key)) == 32

    def test_hkdf_key_differs_from_legacy_key(self, app):
        """HKDF key must NOT be the same as the old truncate/pad key."""
        from gefapi.models.user import User

        User._get_encryption_key.cache_clear()
        with app.app_context():
            hkdf_key = User._get_encryption_key()
            legacy_key = User._get_legacy_encryption_key()
            assert hkdf_key != legacy_key

    def test_encrypt_decrypt_round_trip(self, app):
        """Data encrypted with the new key can be decrypted."""
        from gefapi.models.user import User

        User._get_encryption_key.cache_clear()
        with app.app_context():
            stub = self._make_user_stub("test-roundtrip@example.com")
            plaintext = '{"type": "service_account", "project_id": "test"}'

            encrypted = stub._encrypt_gee_data(plaintext)
            assert encrypted is not None
            assert encrypted != plaintext

            decrypted = stub._decrypt_gee_data(encrypted)
            assert decrypted == plaintext

    def test_legacy_encrypted_data_can_be_decrypted(self, app):
        """Data encrypted with the OLD key must still decrypt (migration)."""
        from cryptography.fernet import Fernet

        from gefapi.models.user import User

        User._get_encryption_key.cache_clear()
        with app.app_context():
            # Encrypt using the legacy key directly
            legacy_key = User._get_legacy_encryption_key()
            fernet = Fernet(legacy_key)
            plaintext = "legacy-secret-data"
            legacy_encrypted = base64.b64encode(
                fernet.encrypt(plaintext.encode("utf-8"))
            ).decode("utf-8")

            # Decrypt should succeed via the fallback path
            stub = self._make_user_stub("test-legacy@example.com")

            decrypted = stub._decrypt_gee_data(legacy_encrypted)
            assert decrypted == plaintext

    def test_encrypt_none_returns_none(self, app):
        from gefapi.models.user import User

        User._get_encryption_key.cache_clear()
        with app.app_context():
            stub = self._make_user_stub()
            assert stub._encrypt_gee_data(None) is None
            assert stub._encrypt_gee_data("") is None

    def test_decrypt_none_returns_none(self, app):
        from gefapi.models.user import User

        with app.app_context():
            stub = self._make_user_stub()
            assert stub._decrypt_gee_data(None) is None
            assert stub._decrypt_gee_data("") is None

    def test_decrypt_garbage_returns_none(self, app):
        """Completely invalid ciphertext should return None, not crash."""
        from gefapi.models.user import User

        User._get_encryption_key.cache_clear()
        with app.app_context():
            stub = self._make_user_stub()
            garbage = base64.b64encode(b"not-valid-fernet-data").decode("utf-8")
            assert stub._decrypt_gee_data(garbage) is None

    def test_missing_key_raises_runtime_error(self):
        """Must raise if no encryption key env var is set."""
        from gefapi.models.user import User

        User._get_encryption_key.cache_clear()
        with patch.dict(
            os.environ,
            {"GEE_ENCRYPTION_KEY": "", "SECRET_KEY": ""},
            clear=False,
        ):
            # Need to clear again since env was patched
            User._get_encryption_key.cache_clear()
            with pytest.raises(RuntimeError, match="Missing encryption key"):
                User._get_encryption_key()


# ---------------------------------------------------------------------------
# C2 — Token blocklist fail-closed in production
# ---------------------------------------------------------------------------


class TestTokenBlocklistFailClosed:
    """C2: Verify blocklist fails closed in production when Redis is down."""

    def _reset_blocklist_state(self):
        """Reset the module-level blocklist state between tests."""
        import gefapi

        gefapi._revoked_tokens.clear()
        gefapi._revoked_tokens_redis_client = None
        gefapi._revoked_tokens_redis_initialized = False

    def test_dev_env_falls_back_to_in_memory(self):
        """In dev, in-memory blocklist is used when Redis is unavailable."""
        self._reset_blocklist_state()
        from gefapi import add_token_to_blocklist, is_token_in_blocklist

        with patch.dict(os.environ, {"ENVIRONMENT": "dev"}):
            self._reset_blocklist_state()
            # Force Redis to be unavailable
            with patch(
                "gefapi.get_revoked_tokens_storage", return_value=None
            ):
                add_token_to_blocklist("test-jti-dev")
                # In dev without Redis, falls back to in-memory
                # But since we patched get_revoked_tokens_storage,
                # the add goes to in-memory
                from gefapi import _revoked_tokens

                assert "test-jti-dev" in _revoked_tokens

    def test_prod_env_is_token_in_blocklist_returns_true_without_redis(self):
        """In production, is_token_in_blocklist must return True (fail closed)
        when Redis is unavailable — even for tokens never explicitly revoked."""
        self._reset_blocklist_state()

        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}):
            self._reset_blocklist_state()
            # Import after reset to get fresh state
            from gefapi import is_token_in_blocklist

            # Force Redis unavailable
            import gefapi

            gefapi._revoked_tokens_redis_client = None
            gefapi._revoked_tokens_redis_initialized = True

            result = is_token_in_blocklist("never-revoked-jti")
            assert result is True, (
                "In production without Redis, all tokens must be treated "
                "as revoked (fail closed)."
            )

    def test_testing_env_allows_in_memory_fallback(self):
        """In testing, in-memory fallback should work normally."""
        self._reset_blocklist_state()

        with patch.dict(os.environ, {"ENVIRONMENT": "testing"}):
            self._reset_blocklist_state()
            from gefapi import (
                _revoked_tokens,
                add_token_to_blocklist,
                is_token_in_blocklist,
            )

            import gefapi

            gefapi._revoked_tokens_redis_client = None
            gefapi._revoked_tokens_redis_initialized = True

            add_token_to_blocklist("test-jti-testing")
            assert "test-jti-testing" in _revoked_tokens
            assert is_token_in_blocklist("test-jti-testing") is True
            assert is_token_in_blocklist("unknown-jti") is False


# ---------------------------------------------------------------------------
# M1 — Generic password recovery response
# ---------------------------------------------------------------------------


class TestGenericPasswordRecoveryResponse:
    """M1: Password recovery returns the same response for all inputs."""

    def test_existing_user_gets_generic_response(
        self, client_no_rate_limiting, regular_user_no_rate_limiting
    ):
        """Recovery for an existing user returns generic message, not user data."""
        response = client_no_rate_limiting.post(
            "/api/v1/user/user@test.com/recover-password"
        )
        assert response.status_code == 200
        data = response.get_json()
        # Must NOT contain user-specific fields like id, email, role
        assert "id" not in str(data)
        assert "role" not in str(data)
        # Must contain the generic message
        assert "message" in data.get("data", {})
        assert "If an account" in data["data"]["message"]

    def test_nonexistent_user_gets_same_generic_response(
        self, client_no_rate_limiting
    ):
        """Recovery for a non-existent user must return 200 with generic message."""
        response = client_no_rate_limiting.post(
            "/api/v1/user/nonexistent-user-12345@example.com/recover-password"
        )
        # Must NOT be 404
        assert response.status_code == 200
        data = response.get_json()
        assert "message" in data.get("data", {})
        assert "If an account" in data["data"]["message"]

    def test_responses_are_indistinguishable(
        self, client_no_rate_limiting, regular_user_no_rate_limiting
    ):
        """Responses for existing and non-existing users must be identical."""
        resp_existing = client_no_rate_limiting.post(
            "/api/v1/user/user@test.com/recover-password"
        )
        resp_missing = client_no_rate_limiting.post(
            "/api/v1/user/definitely-not-a-user@example.com/recover-password"
        )

        assert resp_existing.status_code == resp_missing.status_code == 200

        data_existing = resp_existing.get_json()
        data_missing = resp_missing.get_json()

        assert data_existing == data_missing
