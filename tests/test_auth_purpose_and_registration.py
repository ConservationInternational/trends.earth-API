"""Regression tests for registration authorization and JWT token purposes."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
import uuid

from flask_jwt_extended import create_access_token
import jwt as pyjwt
import pytest

from gefapi.config import SETTINGS
from gefapi.routes.api.v1.unsubscribe import _decode_unsubscribe_token
from gefapi.services.bulk_email_service import (
    _generate_unsubscribe_token,
    _unsubscribe_secret,
)


@pytest.fixture(autouse=True)
def _no_rate_limits(rate_limiting_disabled):
    yield


def _registration_payload(role="USER"):
    return {
        "email": f"registration-{uuid.uuid4()}@example.com",
        "name": "Registration Test",
        "role": role,
    }


def _service_token(user, scopes):
    return create_access_token(
        identity=user.id,
        additional_claims={
            "grant_type": "client_credentials",
            "scopes": scopes,
        },
    )


def test_public_registration_forces_user_role(client):
    payload = _registration_payload(role="SUPERADMIN")
    with patch("gefapi.routes.api.v1.users.UserService.create_user") as create_user:
        create_user.return_value.serialize.return_value = {"role": "USER"}
        response = client.post("/api/v1/user", json=payload)

    assert response.status_code == 200
    assert create_user.call_args.args[0]["role"] == "USER"


@pytest.mark.parametrize("scopes", ["script:read", "user:write"])
def test_restricted_superadmin_service_token_cannot_create_superadmins(
    client, superadmin_user, scopes
):
    token = _service_token(superadmin_user, scopes)
    with patch("gefapi.routes.api.v1.users.UserService.create_user") as create_user:
        response = client.post(
            "/api/v1/user",
            json=_registration_payload(role="SUPERADMIN"),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    create_user.assert_not_called()


def test_admin_write_service_token_can_create_privileged_user(client, superadmin_user):
    token = _service_token(superadmin_user, "admin:write")
    with patch(
        "gefapi.routes.api.v1.users.UserService.create_user",
        return_value=superadmin_user,
    ) as create_user:
        response = client.post(
            "/api/v1/user",
            json=_registration_payload(role="SUPERADMIN"),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert create_user.call_args.args[0]["role"] == "SUPERADMIN"


def _signed_api_token(app, user, claims=None):
    complete_claims = {
        "sub": str(user.id),
        "iat": datetime.now(UTC),
        "nbf": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "jti": str(uuid.uuid4()),
        "type": "access",
        "fresh": False,
        "auth_version": user.auth_version,
    }
    for key, value in (claims or {}).items():
        if key.startswith("missing_"):
            complete_claims.pop(key.removeprefix("missing_"), None)
        else:
            complete_claims[key] = value
    return pyjwt.encode(
        complete_claims, app.config["JWT_SECRET_KEY"], algorithm="HS256"
    )


def _get_me(client, token):
    return client.get("/api/v1/user/me", headers={"Authorization": f"Bearer {token}"})


def test_raw_signed_access_token_for_real_user_authenticates(app, client, regular_user):
    with app.app_context():
        token = _signed_api_token(app, regular_user)
    response = _get_me(client, token)
    assert response.status_code == 200
    assert response.get_json()["data"]["id"] == str(regular_user.id)


def test_ordinary_flask_jwt_extended_token_without_auth_version_still_works(
    app, client, regular_user
):
    with app.app_context():
        token = create_access_token(identity=regular_user.id)
    assert _get_me(client, token).status_code == 200


@pytest.mark.parametrize(
    "claims",
    [
        {"missing_type": True},
        {"missing_jti": True},
        {"jti": ""},
        {"purpose": "unsubscribe"},
        {"type": "refresh"},
    ],
)
def test_malformed_or_wrong_purpose_hs_tokens_do_not_authenticate(
    app, client, regular_user, claims
):
    with app.app_context():
        token = _signed_api_token(app, regular_user, claims)
    response = _get_me(client, token)
    assert response.status_code == 401


def test_historical_shared_key_unsubscribe_token_with_access_shape_is_rejected(
    app, client, regular_user
):
    with app.app_context():
        token = _signed_api_token(app, regular_user, {"purpose": "unsubscribe"})
    assert _get_me(client, token).status_code == 401


def test_unsubscribe_token_is_domain_separated_and_cannot_authenticate(
    app, client, regular_user
):
    with (
        app.app_context(),
        patch.dict(
            SETTINGS,
            {
                "UNSUBSCRIBE_JWT_SECRET": None,
                "JWT_SECRET_KEY": app.config["JWT_SECRET_KEY"],
            },
        ),
    ):
        token = _generate_unsubscribe_token(regular_user.id)
        assert _unsubscribe_secret() != SETTINGS["JWT_SECRET_KEY"]
        response = client.get(
            "/api/v1/user/me", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 401

    with (
        app.app_context(),
        patch.dict(
            SETTINGS,
            {
                "UNSUBSCRIBE_JWT_SECRET": None,
                "JWT_SECRET_KEY": app.config["JWT_SECRET_KEY"],
            },
        ),
    ):
        user_id, err = _decode_unsubscribe_token(token)
    assert err is None
    assert user_id == str(regular_user.id)


def test_legacy_api_signed_unsubscribe_link_works_only_at_unsubscribe_endpoint(
    app, client, regular_user
):
    with app.app_context():
        legacy_token = pyjwt.encode(
            {
                "sub": str(regular_user.id),
                "purpose": "unsubscribe",
                "exp": datetime.now(UTC) + timedelta(days=1),
            },
            app.config["JWT_SECRET_KEY"],
            algorithm="HS256",
        )

    auth_response = client.get(
        "/api/v1/user/me",
        headers={"Authorization": f"Bearer {legacy_token}"},
    )
    assert auth_response.status_code == 401

    with (
        app.app_context(),
        patch.dict(
            SETTINGS,
            {
                "UNSUBSCRIBE_JWT_SECRET": None,
                "JWT_SECRET_KEY": app.config["JWT_SECRET_KEY"],
            },
        ),
    ):
        unsubscribe_response = client.get(f"/api/v1/unsubscribe?token={legacy_token}")
    assert unsubscribe_response.status_code == 200


def test_plugin_login_and_legacy_refresh_remain_usable(
    client_no_rate_limiting, regular_user_no_rate_limiting
):
    del regular_user_no_rate_limiting
    login = client_no_rate_limiting.post(
        "/auth",
        json={"email": "user@test.com", "password": "UserPass123!"},
    )
    assert login.status_code == 200
    login_data = login.get_json()
    assert {"access_token", "refresh_token", "user_id", "expires_in"} <= set(login_data)

    access_headers = {"Authorization": f"Bearer {login_data['access_token']}"}
    assert (
        client_no_rate_limiting.get(
            "/api/v1/user/me", headers=access_headers
        ).status_code
        == 200
    )

    refreshed = client_no_rate_limiting.post(
        "/auth/refresh?legacy=false",
        json={"refresh_token": login_data["refresh_token"]},
    )
    assert refreshed.status_code == 200
    refreshed_data = refreshed.get_json()
    assert {"access_token", "refresh_token", "user_id", "expires_in"} <= set(
        refreshed_data
    )
    assert (
        client_no_rate_limiting.get(
            "/api/v1/user/me",
            headers={"Authorization": f"Bearer {refreshed_data['access_token']}"},
        ).status_code
        == 200
    )
