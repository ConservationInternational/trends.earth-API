"""
Tests for OAuth2 Client Credentials service client model, service, and routes.
"""

from unittest.mock import Mock, patch
import uuid

import pytest

from gefapi import db
from gefapi.errors import AuthError, NotAllowed
from gefapi.models.service_client import (
    CLIENT_ID_PREFIX,
    CLIENT_SECRET_PREFIX,
    ServiceClient,
)
from gefapi.services.oauth2_service import MAX_CLIENTS_PER_USER, OAuth2Service


@pytest.fixture(autouse=True)
def _clean_service_clients(app):
    """Remove all service_client rows between tests to avoid state leaking."""
    yield
    with app.app_context():
        try:
            ServiceClient.query.delete()
            db.session.commit()
        except Exception:
            db.session.rollback()


# -------------------------------------------------------------------------
# Model unit tests
# -------------------------------------------------------------------------


class TestServiceClientModel:
    """Tests for the ServiceClient model class methods."""

    def test_generate_credentials_format(self):
        """Generated credentials have the expected prefixes and lengths."""
        client_id, raw_secret, secret_hash = ServiceClient.generate_credentials()

        assert client_id.startswith(CLIENT_ID_PREFIX)
        assert raw_secret.startswith(CLIENT_SECRET_PREFIX)
        assert len(secret_hash) == 64  # SHA-256 hex digest

    def test_generate_credentials_unique(self):
        """Each call produces unique credentials."""
        creds_a = ServiceClient.generate_credentials()
        creds_b = ServiceClient.generate_credentials()
        assert creds_a[0] != creds_b[0]  # client_id
        assert creds_a[1] != creds_b[1]  # raw_secret

    def test_hash_secret_deterministic(self):
        """Hashing the same secret twice gives the same result."""
        secret = "te_cs_abc123"
        h1 = ServiceClient.hash_secret(secret)
        h2 = ServiceClient.hash_secret(secret)
        assert h1 == h2

    def test_verify_secret_correct(self):
        """verify_secret returns True for the matching raw secret."""
        _, raw_secret, secret_hash = ServiceClient.generate_credentials()
        client = ServiceClient(client_secret_hash=secret_hash)
        assert client.verify_secret(raw_secret) is True

    def test_verify_secret_wrong(self):
        """verify_secret returns False for a wrong secret."""
        _, _, secret_hash = ServiceClient.generate_credentials()
        client = ServiceClient(client_secret_hash=secret_hash)
        assert client.verify_secret("te_cs_wrong") is False

    def test_is_valid_active_client(self):
        """A non-revoked, non-expired client is valid."""
        client = ServiceClient(revoked=False, expires_at=None)
        assert client.is_valid() is True

    def test_is_valid_revoked(self):
        """A revoked client is not valid."""
        client = ServiceClient(revoked=True, expires_at=None)
        assert client.is_valid() is False

    def test_is_expired_no_expiry(self):
        """A client with no expires_at is never expired."""
        client = ServiceClient(expires_at=None)
        assert client.is_expired() is False

    def test_is_expired_past(self):
        """A client with an expires_at in the past is expired."""
        import datetime

        past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        client = ServiceClient(expires_at=past)
        assert client.is_expired() is True

    def test_is_expired_future(self):
        """A client with an expires_at in the future is not expired."""
        import datetime

        future = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
        client = ServiceClient(expires_at=future)
        assert client.is_expired() is False

    def test_serialize_contains_expected_keys(self):
        """serialize() returns the expected dict keys."""
        client = ServiceClient(
            id="abc",
            name="Test",
            client_id="te_cid_x",
            secret_prefix="abcdef12",
            scopes="read write",
            user_id="u1",
            revoked=False,
        )
        data = client.serialize()
        expected_keys = {
            "id",
            "name",
            "client_id",
            "secret_prefix",
            "scopes",
            "user_id",
            "created_at",
            "last_used_at",
            "expires_at",
            "revoked",
        }
        assert set(data.keys()) == expected_keys

    def test_serialize_never_contains_secret(self):
        """serialize() must never expose the secret hash."""
        client = ServiceClient(
            id="abc",
            name="Test",
            client_id="te_cid_x",
            client_secret_hash="deadbeef",
            secret_prefix="abcdef12",
            scopes="",
            user_id="u1",
            revoked=False,
        )
        data = client.serialize()
        assert "client_secret_hash" not in data
        assert "client_secret" not in data


# -------------------------------------------------------------------------
# Service unit tests (database-backed)
# -------------------------------------------------------------------------


class TestOAuth2ServiceCreateClient:
    """Tests for OAuth2Service.create_client."""

    def test_create_client_success(self, app, regular_user):
        """Creating a client returns raw_secret and persists the client."""
        with app.app_context():
            user = db.session.merge(regular_user)
            raw_secret, client = OAuth2Service.create_client(
                user=user, name="My Service"
            )

            assert raw_secret.startswith(CLIENT_SECRET_PREFIX)
            assert client.name == "My Service"
            assert client.client_id.startswith(CLIENT_ID_PREFIX)
            assert client.user_id == user.id
            assert client.revoked is False
            assert client.scopes == ""

    def test_create_client_with_scopes(self, app, regular_user):
        """Scopes are persisted correctly."""
        with app.app_context():
            user = db.session.merge(regular_user)
            _, client = OAuth2Service.create_client(
                user=user, name="Scoped", scopes="executions:read"
            )
            assert client.scopes == "executions:read"

    def test_create_client_with_expiry(self, app, regular_user):
        """expires_at is set when expires_in_days is provided."""
        with app.app_context():
            user = db.session.merge(regular_user)
            _, client = OAuth2Service.create_client(
                user=user, name="Expiring", expires_in_days=30
            )
            assert client.expires_at is not None

    def test_create_client_enforces_limit(self, app, regular_user):
        """Cannot exceed MAX_CLIENTS_PER_USER active clients."""
        with app.app_context():
            user = db.session.merge(regular_user)
            for i in range(MAX_CLIENTS_PER_USER):
                OAuth2Service.create_client(user=user, name=f"svc-{i}")

            with pytest.raises(NotAllowed):
                OAuth2Service.create_client(user=user, name="over-limit")

    def test_revoked_clients_dont_count_toward_limit(self, app, regular_user):
        """Revoked clients do not count toward the active-client limit."""
        with app.app_context():
            user = db.session.merge(regular_user)
            for i in range(MAX_CLIENTS_PER_USER):
                _, client = OAuth2Service.create_client(user=user, name=f"svc-{i}")
            # Revoke one
            OAuth2Service.revoke_client(client.id, user)
            # Should succeed now
            _, new_client = OAuth2Service.create_client(user=user, name="replacement")
            assert new_client.revoked is False


class TestOAuth2ServiceListClients:
    """Tests for OAuth2Service.list_clients."""

    def test_list_returns_only_active(self, app, regular_user):
        """list_clients excludes revoked clients."""
        with app.app_context():
            user = db.session.merge(regular_user)
            _, c1 = OAuth2Service.create_client(user=user, name="active")
            _, c2 = OAuth2Service.create_client(user=user, name="revoked")
            OAuth2Service.revoke_client(c2.id, user)

            clients = OAuth2Service.list_clients(user)
            ids = [c.id for c in clients]
            assert c1.id in ids
            assert c2.id not in ids


class TestOAuth2ServiceRevokeClient:
    """Tests for OAuth2Service.revoke_client."""

    def test_revoke_own_client(self, app, regular_user):
        """A user can revoke their own client."""
        with app.app_context():
            user = db.session.merge(regular_user)
            _, client = OAuth2Service.create_client(user=user, name="to-revoke")
            revoked = OAuth2Service.revoke_client(client.id, user)
            assert revoked.revoked is True

    def test_revoke_nonexistent_raises(self, app, regular_user):
        """Revoking a non-existent client raises NotAllowed."""
        with app.app_context():
            user = db.session.merge(regular_user)
            fake_uuid = str(uuid.uuid4())
            with pytest.raises(NotAllowed):
                OAuth2Service.revoke_client(fake_uuid, user)

    def test_revoke_other_users_client_denied(self, app, regular_user, admin_user):
        """A non-admin user cannot revoke another user's client."""
        with app.app_context():
            owner = db.session.merge(regular_user)
            db.session.merge(admin_user)
            # admin_user has role ADMIN, which is admin_or_higher => allowed
            # Use two regular users instead; we'll mock is_admin_or_higher
            _, client = OAuth2Service.create_client(user=owner, name="private")
            with patch(
                "gefapi.utils.permissions.is_admin_or_higher",
                return_value=False,
            ):
                mock_other = Mock()
                mock_other.id = str(uuid.uuid4())
                with pytest.raises(NotAllowed):
                    OAuth2Service.revoke_client(client.id, mock_other)

    def test_admin_can_revoke_any_client(self, app, regular_user, admin_user):
        """An admin can revoke any user's client."""
        with app.app_context():
            owner = db.session.merge(regular_user)
            admin = db.session.merge(admin_user)
            _, client = OAuth2Service.create_client(user=owner, name="others-client")
            revoked = OAuth2Service.revoke_client(client.id, admin)
            assert revoked.revoked is True


class TestOAuth2ServiceAuthenticate:
    """Tests for OAuth2Service.authenticate."""

    def test_authenticate_valid_credentials(self, app, regular_user):
        """Valid client_id + client_secret returns the owning user."""
        with app.app_context():
            user = db.session.merge(regular_user)
            raw_secret, client = OAuth2Service.create_client(
                user=user, name="auth-test"
            )
            authed_user = OAuth2Service.authenticate(client.client_id, raw_secret)
            assert authed_user.id == user.id

    def test_authenticate_wrong_secret(self, app, regular_user):
        """Wrong client_secret raises AuthError."""
        with app.app_context():
            user = db.session.merge(regular_user)
            _, client = OAuth2Service.create_client(user=user, name="bad-secret")
            with pytest.raises(AuthError):
                OAuth2Service.authenticate(client.client_id, "te_cs_wrong")

    def test_authenticate_unknown_client_id(self, app):
        """Unknown client_id raises AuthError."""
        with app.app_context(), pytest.raises(AuthError):
            OAuth2Service.authenticate("te_cid_unknown", "te_cs_nope")

    def test_authenticate_revoked_client(self, app, regular_user):
        """Revoked client raises AuthError."""
        with app.app_context():
            user = db.session.merge(regular_user)
            raw_secret, client = OAuth2Service.create_client(user=user, name="revoked")
            OAuth2Service.revoke_client(client.id, user)
            with pytest.raises(AuthError):
                OAuth2Service.authenticate(client.client_id, raw_secret)

    def test_authenticate_expired_client(self, app, regular_user):
        """Expired client raises AuthError."""
        import datetime

        with app.app_context():
            user = db.session.merge(regular_user)
            raw_secret, client = OAuth2Service.create_client(user=user, name="expired")
            # Force-expire the client
            client.expires_at = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
            db.session.commit()
            with pytest.raises(AuthError):
                OAuth2Service.authenticate(client.client_id, raw_secret)


# -------------------------------------------------------------------------
# Route / endpoint tests
# -------------------------------------------------------------------------


class TestOAuth2TokenEndpoint:
    """Tests for POST /api/v1/oauth/token."""

    def test_token_exchange_success(self, app, client, regular_user):
        """Valid client credentials yield an access_token."""
        with app.app_context():
            user = db.session.merge(regular_user)
            raw_secret, svc = OAuth2Service.create_client(user=user, name="token-test")
            svc_client_id = svc.client_id

        resp = client.post(
            "/api/v1/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": svc_client_id,
                "client_secret": raw_secret,
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0

    def test_token_exchange_json_body(self, app, client, regular_user):
        """Token endpoint also accepts JSON body."""
        with app.app_context():
            user = db.session.merge(regular_user)
            raw_secret, svc = OAuth2Service.create_client(user=user, name="json-test")
            svc_client_id = svc.client_id

        resp = client.post(
            "/api/v1/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": svc_client_id,
                "client_secret": raw_secret,
            },
        )
        assert resp.status_code == 200
        assert "access_token" in resp.get_json()

    def test_token_wrong_grant_type(self, client):
        """Non-client_credentials grant_type returns 400."""
        resp = client.post(
            "/api/v1/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": "x",
                "client_secret": "y",
            },
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "unsupported_grant_type"

    def test_token_missing_credentials(self, client):
        """Missing client_id/client_secret returns 400."""
        resp = client.post(
            "/api/v1/oauth/token",
            data={"grant_type": "client_credentials"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_request"

    def test_token_invalid_credentials(self, client):
        """Bad credentials return 401."""
        resp = client.post(
            "/api/v1/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "te_cid_fake",
                "client_secret": "te_cs_fake",
            },
        )
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "invalid_client"

    def test_token_can_access_protected_endpoint(self, app, client, regular_user):
        """A token obtained via client_credentials can access /user/me."""
        with app.app_context():
            user = db.session.merge(regular_user)
            raw_secret, svc = OAuth2Service.create_client(user=user, name="e2e-test")
            svc_client_id = svc.client_id

        # Exchange for token
        token_resp = client.post(
            "/api/v1/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": svc_client_id,
                "client_secret": raw_secret,
            },
        )
        access_token = token_resp.get_json()["access_token"]

        # Use token to hit a protected endpoint
        me_resp = client.get(
            "/api/v1/user/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert me_resp.status_code == 200
        assert me_resp.get_json()["data"]["email"] == "user@test.com"


class TestOAuth2ClientManagementEndpoints:
    """Tests for the /api/v1/oauth/clients management routes."""

    def test_create_client_endpoint(self, client, auth_headers_user):
        """POST /oauth/clients creates a client and returns raw secret."""
        resp = client.post(
            "/api/v1/oauth/clients",
            json={"name": "My CLI Tool"},
            headers=auth_headers_user,
        )
        assert resp.status_code == 201
        data = resp.get_json()["data"]
        assert "client_id" in data
        assert "client_secret" in data  # one-time disclosure
        assert data["name"] == "My CLI Tool"
        assert data["revoked"] is False

    def test_create_client_with_scopes_and_expiry(self, client, auth_headers_user):
        """POST /oauth/clients with scopes and expires_in_days."""
        resp = client.post(
            "/api/v1/oauth/clients",
            json={
                "name": "Scoped",
                "scopes": "exec:read",
                "expires_in_days": 90,
            },
            headers=auth_headers_user,
        )
        assert resp.status_code == 201
        data = resp.get_json()["data"]
        assert data["scopes"] == "exec:read"
        assert data["expires_at"] is not None

    def test_create_client_missing_name(self, client, auth_headers_user):
        """POST /oauth/clients without name returns 400."""
        resp = client.post(
            "/api/v1/oauth/clients",
            json={},
            headers=auth_headers_user,
        )
        assert resp.status_code == 400

    def test_create_client_bad_expiry(self, client, auth_headers_user):
        """POST /oauth/clients with invalid expires_in_days returns 400."""
        resp = client.post(
            "/api/v1/oauth/clients",
            json={"name": "Bad", "expires_in_days": -1},
            headers=auth_headers_user,
        )
        assert resp.status_code == 400

    def test_create_client_requires_auth(self, client):
        """POST /oauth/clients without JWT returns 401."""
        resp = client.post(
            "/api/v1/oauth/clients",
            json={"name": "No Auth"},
        )
        assert resp.status_code == 401

    def test_list_clients_endpoint(self, client, auth_headers_user):
        """GET /oauth/clients lists clients without secrets."""
        # Create one first
        client.post(
            "/api/v1/oauth/clients",
            json={"name": "List Test"},
            headers=auth_headers_user,
        )

        resp = client.get(
            "/api/v1/oauth/clients",
            headers=auth_headers_user,
        )
        assert resp.status_code == 200
        items = resp.get_json()["data"]
        assert len(items) >= 1
        for item in items:
            assert "client_secret" not in item
            assert "client_secret_hash" not in item

    def test_list_clients_requires_auth(self, client):
        """GET /oauth/clients without JWT returns 401."""
        resp = client.get("/api/v1/oauth/clients")
        assert resp.status_code == 401

    def test_revoke_client_endpoint(self, client, auth_headers_user):
        """DELETE /oauth/clients/<id> revokes the client."""
        create_resp = client.post(
            "/api/v1/oauth/clients",
            json={"name": "Revoke Me"},
            headers=auth_headers_user,
        )
        client_db_id = create_resp.get_json()["data"]["id"]

        del_resp = client.delete(
            f"/api/v1/oauth/clients/{client_db_id}",
            headers=auth_headers_user,
        )
        assert del_resp.status_code == 200
        assert del_resp.get_json()["data"]["revoked"] is True

    def test_revoke_client_requires_auth(self, client):
        """DELETE /oauth/clients/<id> without JWT returns 401."""
        resp = client.delete("/api/v1/oauth/clients/fake-id")
        assert resp.status_code == 401

    def test_revoke_nonexistent_client(self, client, auth_headers_user):
        """DELETE /oauth/clients/<bad-id> returns 400."""
        fake_uuid = str(uuid.uuid4())
        resp = client.delete(
            f"/api/v1/oauth/clients/{fake_uuid}",
            headers=auth_headers_user,
        )
        assert resp.status_code == 400
