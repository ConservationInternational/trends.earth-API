"""Offline regression tests for user-controlled GEE credential endpoints."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth.exceptions import RefreshError
import pytest
import requests

from gefapi.services import gee_service
from gefapi.services.gee_service import GEEService

pytestmark = pytest.mark.standalone

TOKEN_URI = "https://oauth2.googleapis.com/token"


@pytest.fixture(scope="module")
def standard_key():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {
        "type": "service_account",
        "project_id": "test-project",
        "private_key_id": "synthetic-key-id",
        "private_key": private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        "client_email": "test@test-project.iam.gserviceaccount.com",
        "client_id": "123456789",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": TOKEN_URI,
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": (
            "https://www.googleapis.com/robot/v1/metadata/x509/"
            "test%40test-project.iam.gserviceaccount.com"
        ),
        "universe_domain": "googleapis.com",
    }


@pytest.mark.parametrize(
    "token_uri",
    [
        "http://127.0.0.1:8123/token",
        "http://169.254.169.254/latest/meta-data/",
        "https://10.0.0.1/token",
        "https://192.168.0.1/token",
        "https://[::1]/token",
        "https://attacker.example/token",
        "http://oauth2.googleapis.com/token",
        "https://oauth2.googleapis.com.attacker.example/token",
        "https://oauth2.googleapis.com@attacker.example/token",
        "https://attacker.example@oauth2.googleapis.com/token",
        "https://oauth2.googleapis.com:443/token",
        "https://oauth2.googleapis.com/token/",
        "https://oauth2.googleapis.com/token?redirect_uri=http://127.0.0.1",
        "https://oauth2.googleapis.com/token#fragment",
        "https://oauth2.googleapis.com./token",
        "https://OAUTH2.GOOGLEAPIS.COM/token",
        " https://oauth2.googleapis.com/token",
        "https://oauth2.googleapis.com/token\n",
        "file:///etc/passwd",
    ],
)
def test_upload_validation_rejects_untrusted_token_endpoints(
    standard_key, token_uri, caplog
):
    assert not GEEService.validate_service_account_key(
        {**standard_key, "token_uri": token_uri}
    )
    assert "token_uri" in caplog.text
    assert TOKEN_URI in caplog.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("universe_domain", "attacker.example"),
        ("universe_domain", "googleapis.cn"),
        ("universe_domain", "GOOGLEAPIS.COM"),
        ("universe_domain", "googleapis.com."),
        ("universe_domain", ""),
        ("universe_domain", None),
        ("auth_uri", "http://127.0.0.1/auth"),
        ("auth_provider_x509_cert_url", "https://attacker.example/certs"),
        ("client_x509_cert_url", "http://127.0.0.1/certs"),
        ("client_x509_cert_url", "https://www.googleapis.com/other"),
        ("token_info_url", "http://127.0.0.1/info"),
        ("service_account_impersonation_url", "http://127.0.0.1/impersonate"),
        ("credential_source", {"url": "http://169.254.169.254"}),
        ("universe_domain_url", "http://127.0.0.1/universe"),
        ("type", "external_account"),
        ("type", "impersonated_service_account"),
        ("token_uri", ["https://oauth2.googleapis.com/token"]),
        ("private_key", 123),
    ],
)
def test_upload_validation_rejects_unsupported_schema_and_endpoints(
    standard_key, field, value
):
    assert not GEEService.validate_service_account_key({**standard_key, field: value})


@pytest.mark.parametrize("data", [None, [], "service_account", {}, 123])
def test_upload_validation_rejects_non_key_documents(data):
    assert not GEEService.validate_service_account_key(data)


@pytest.mark.parametrize(
    "token_uri",
    [
        TOKEN_URI,
        "https://accounts.google.com/o/oauth2/token",
        "https://www.googleapis.com/oauth2/v4/token",
    ],
)
def test_standard_and_legacy_google_keys_use_server_selected_endpoint(
    standard_key, token_uri
):
    key = {**standard_key, "token_uri": token_uri}
    assert GEEService.validate_service_account_key(key)
    validated = GEEService.sanitize_service_account_key(key)
    assert validated["token_uri"] == TOKEN_URI
    assert "client_x509_cert_url" not in validated
    assert key["token_uri"] == token_uri


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("token_uri", "http://127.0.0.1/token"),
        ("universe_domain", "attacker.example"),
        ("credential_source", {"url": "http://169.254.169.254"}),
    ],
)
def test_workload_sanitizer_rejects_unsafe_stored_keys(standard_key, field, value):
    with pytest.raises(ValueError):
        GEEService.sanitize_service_account_key({**standard_key, field: value})


def test_serialized_workload_credentials_refresh_only_at_google(standard_key):
    from google.oauth2 import service_account

    document = {
        **standard_key,
        "token_uri": "https://accounts.google.com/o/oauth2/token",
    }
    encoded_document = json.dumps(GEEService.sanitize_service_account_key(document))
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(encoded_document), scopes=gee_service.ee.oauth.SCOPES
    )
    request = Mock(
        return_value=SimpleNamespace(
            status=200,
            data=json.dumps(
                {"access_token": "synthetic-access-token", "expires_in": 3600}
            ).encode(),
        )
    )
    credentials.refresh(request)
    request.assert_called_once()
    assert request.call_args.kwargs["url"] == TOKEN_URI
    assert credentials.universe_domain == "googleapis.com"


def test_standard_key_without_optional_fields_is_supported(standard_key):
    key = {
        field: value
        for field, value in standard_key.items()
        if field
        not in {
            "auth_provider_x509_cert_url",
            "client_x509_cert_url",
            "universe_domain",
        }
    }
    assert GEEService.validate_service_account_key(key)


@pytest.mark.parametrize("already_initialized", [False, True])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("token_uri", "http://127.0.0.1/token"),
        ("universe_domain", "attacker.example"),
        ("credential_source", {"url": "http://169.254.169.254"}),
        ("auth_provider_x509_cert_url", "https://attacker.example/certs"),
    ],
)
def test_stored_unsafe_key_is_rejected_before_any_ee_request(
    standard_key, already_initialized, field, value
):
    user = SimpleNamespace(
        email="test@example.com",
        gee_credentials_type="service_account",
        has_gee_credentials=Mock(return_value=True),
        get_gee_service_account=Mock(return_value={**standard_key, field: value}),
    )
    with patch.object(gee_service, "ee") as mock_ee:
        if not already_initialized:
            mock_ee.data.listOperations.side_effect = RuntimeError("Not initialized")
        assert not GEEService._initialize_ee(user)
        assert not GEEService._initialize_ee_with_user_service_account(user)
        mock_ee.data.listOperations.assert_not_called()
        mock_ee.ServiceAccountCredentials.assert_not_called()
        mock_ee.Initialize.assert_not_called()


def test_user_initialization_builds_pinned_credentials_in_memory(standard_key):
    user = SimpleNamespace(
        email="test@example.com",
        get_gee_service_account=Mock(return_value=standard_key),
    )
    # Keep real EE scope constants while ensuring initialization is entirely local.
    with (
        patch.object(gee_service.ee, "Initialize") as initialize,
        patch.object(gee_service.tempfile, "NamedTemporaryFile") as temporary_file,
    ):
        assert GEEService._initialize_ee_with_user_service_account(user)
        credentials = initialize.call_args.args[0]
        assert credentials.service_account_email == standard_key["client_email"]
        assert credentials._token_uri == TOKEN_URI
        temporary_file.assert_not_called()


def token_response(status, data, location=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(data).encode()
    if location:
        response.headers["Location"] = location
    return response


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/token",
        "http://169.254.169.254/latest/meta-data/",
        "https://10.0.0.1/token",
        "https://attacker.example/token",
        TOKEN_URI,
    ],
)
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_token_refresh_refuses_redirects(standard_key, location, status):
    credentials = gee_service._service_account_credentials(
        GEEService._validated_service_account_key(standard_key)
    )
    # EE and google-auth can clone credentials when setting scopes.
    credentials = credentials.with_scopes(gee_service.ee.oauth.SCOPES)
    supplied_transport = Mock(side_effect=AssertionError("Unexpected EE transport"))
    with (
        patch(
            "requests.Session.request",
            return_value=token_response(status, {}, location),
        ) as request,
        pytest.raises(RefreshError, match="redirect"),
    ):
        credentials.refresh(supplied_transport)
    request.assert_called_once()
    assert request.call_args.args == ("POST", TOKEN_URI)
    assert request.call_args.kwargs["allow_redirects"] is False
    supplied_transport.assert_not_called()


def test_token_refresh_succeeds_at_pinned_endpoint(standard_key):
    credentials = gee_service._service_account_credentials(
        GEEService._validated_service_account_key(standard_key)
    )
    with patch(
        "requests.Session.request",
        return_value=token_response(
            200, {"access_token": "synthetic-access-token", "expires_in": 3600}
        ),
    ) as request:
        credentials.refresh(Mock())
    assert credentials.token == "synthetic-access-token"
    assert request.call_args.args == ("POST", TOKEN_URI)
    assert request.call_args.kwargs["allow_redirects"] is False


def test_changed_credential_token_uri_cannot_bypass_refresh_guard(standard_key):
    credentials = gee_service._service_account_credentials(
        GEEService._validated_service_account_key(standard_key)
    ).with_token_uri("http://127.0.0.1/token")
    with (
        patch("requests.Session.request") as request,
        pytest.raises(RefreshError, match="standard Google HTTPS token endpoint"),
    ):
        credentials.refresh(Mock())
    request.assert_not_called()


def test_oauth_configured_token_uri_is_unchanged(monkeypatch):
    monkeypatch.setitem(
        gee_service.SETTINGS,
        "environment",
        {"GOOGLE_OAUTH_TOKEN_URI": "https://oauth.example/token"},
    )
    assert GEEService._oauth_token_uri() == "https://oauth.example/token"
