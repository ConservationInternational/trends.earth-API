"""Contract tests for the application-scoped user-token API.

``/api/v1/apps/<app_key>/*`` is one generic implementation shared by every
registered application.  Every test that is not explicitly about a
per-application policy difference runs for both Rio Coherence and Avoided
Emissions.  Requests are only authorized with an RS256 OIDC access token
issued to a client bound to the URL application, carrying the operation's
scopes (granted *and* still registered for the client), for a current,
verified, eligible account.
"""

from dataclasses import dataclass
from unittest.mock import patch
import urllib.parse
import uuid

from flask_jwt_extended import create_access_token
import pytest

from gefapi import add_token_to_blocklist, db
from gefapi.models import OAuthClient, User
from gefapi.models.app_access import (
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REVOKED,
    UserAppAccess,
)
from gefapi.services.app_access_service import AppAccessService
from gefapi.services.oidc_service import decode_token, issue_token
from gefapi.utils.app_access import APP_POLICIES, PROFILE_RESPONSE_FIELDS, AppPolicy
from tests.conftest import ENVIRONMENT_USER_EMAIL, USER_TEST_PASSWORD

RIO = "rio_coherence"
AE = "avoided_emissions"
BASE_SCOPE = "openid email profile"
ALL_SCOPES = (
    "openid email profile app:read account:write app-users:read app-access:write"
)
IDENTITY_FIELDS = {"sub", "email", "email_verified", "name", "institution", "active"}
PROFILE_URL = "/api/v1/account/profile"


@pytest.fixture(autouse=True)
def _no_rate_limits(rate_limiting_disabled):
    yield


def _oauth_client(client_id, required_app_key, scopes=ALL_SCOPES, audience=None):
    client = OAuthClient(
        name=client_id,
        client_id=client_id,
        redirect_uris="https://app.test/cb",
        post_logout_redirect_uris="",
        audience=audience or f"{client_id}-aud",
        scopes=scopes,
        is_public=True,
        required_app_key=required_app_key,
    )
    db.session.add(client)
    db.session.commit()
    return client


def _user(email, name="Someone", role="USER", verified=True, active=True):
    user = User(
        email=email,
        password=USER_TEST_PASSWORD,
        name=name,
        role=role,
        country="US",
        institution="Inst",
    )
    user.email_verified = verified
    user.is_active = active
    db.session.add(user)
    db.session.commit()
    return user


def _grant(user, app_key, status=STATUS_ACTIVE, role="member"):
    db.session.add(
        UserAppAccess(user_id=user.id, app_key=app_key, status=status, role=role)
    )
    db.session.commit()


def _headers(user, client, scope=ALL_SCOPES):
    token = issue_token(user, client, "access_token", 300, scope)
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Ctx:
    key: str
    other: str
    base: str
    client: OAuthClient
    member: User
    admin: User
    superadmin: User

    @property
    def policy(self):
        return APP_POLICIES[self.key]

    def h(self, user, scope=ALL_SCOPES):
        return _headers(user, self.client, scope)


@pytest.fixture(params=[RIO, AE])
def ctx(request, app):
    key = request.param
    other = AE if key == RIO else RIO
    client = _oauth_client(f"{key}-web", key)
    member = _user("member@app.test", name="App Member")
    _grant(member, key)
    admin = _user("app-admin@app.test", name="App Admin")
    _grant(admin, key, role="admin")
    superadmin = _user("root@app.test", name="Root", role="SUPERADMIN")
    return Ctx(key, other, f"/api/v1/apps/{key}", client, member, admin, superadmin)


def _put_access(client, ctx, headers, subject, body):
    return client.put(f"{ctx.base}/users/{subject}/access", json=body, headers=headers)


def _resolve(client, ctx, headers, subjects):
    return client.post(
        f"{ctx.base}/users/resolve", json={"subjects": subjects}, headers=headers
    )


def _service_token_headers(user):
    token = create_access_token(
        identity=user.id,
        additional_claims={"grant_type": "client_credentials", "scopes": "user:read"},
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Authentication, client binding, unknown apps, and token types
# ---------------------------------------------------------------------------


def test_missing_token_is_401(client, ctx):
    assert client.get(f"{ctx.base}/me").status_code == 401
    assert client.patch(PROFILE_URL, json={"name": "X"}).status_code == 401


@pytest.mark.parametrize(
    "method,suffix",
    [("get", "/me"), ("get", "/users"), ("post", "/users/resolve")],
)
def test_legacy_hs256_tokens_are_rejected(
    client, ctx, auth_headers_superadmin, method, suffix
):
    response = getattr(client, method)(
        f"{ctx.base}{suffix}", json={"subjects": []}, headers=auth_headers_superadmin
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "client_identity_required"


def test_legacy_token_cannot_write_access_or_profile(
    client, ctx, auth_headers_superadmin
):
    response = _put_access(
        client, ctx, auth_headers_superadmin, ctx.member.id, {"status": "revoked"}
    )
    assert response.get_json()["error"] == "client_identity_required"
    response = client.patch(
        PROFILE_URL, json={"name": "X"}, headers=auth_headers_superadmin
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "client_identity_required"


def test_service_client_token_is_rejected(client, ctx):
    headers = _service_token_headers(ctx.superadmin)
    response = client.get(f"{ctx.base}/users", headers=headers)
    assert response.status_code == 403
    assert response.get_json()["error"] == "client_identity_required"
    response = client.patch(PROFILE_URL, json={"name": "X"}, headers=headers)
    assert response.status_code == 403
    assert response.get_json()["error"] == "client_identity_required"


def test_id_token_is_not_an_api_credential(client, ctx):
    token = issue_token(ctx.member, ctx.client, "id_token", 300, ALL_SCOPES, "n")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get(f"{ctx.base}/me", headers=headers).status_code == 401
    assert (
        client.patch(PROFILE_URL, json={"name": "X"}, headers=headers).status_code
        == 401
    )


def test_cross_app_client_is_rejected_even_with_both_grants(client, ctx):
    _grant(ctx.member, ctx.other)
    other_client = _oauth_client("other-web", ctx.other)
    headers = _headers(ctx.member, other_client)
    for method, suffix in (("get", "/me"), ("get", "/users")):
        response = getattr(client, method)(f"{ctx.base}{suffix}", headers=headers)
        assert response.status_code == 403
        assert response.get_json()["error"] == "client_app_mismatch"
    response = _put_access(
        client,
        ctx,
        _headers(ctx.superadmin, other_client),
        ctx.member.id,
        {"status": "revoked"},
    )
    assert response.get_json()["error"] == "client_app_mismatch"
    assert (
        UserAppAccess.query.filter_by(user_id=ctx.member.id, app_key=ctx.key)
        .one()
        .status
        == STATUS_ACTIVE
    )


def test_ungated_client_is_rejected(client, ctx):
    ungated = _oauth_client("te-ui", None, scopes="openid email profile account:write")
    response = client.get(
        f"{ctx.base}/me", headers=_headers(ctx.member, ungated, BASE_SCOPE)
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "client_app_mismatch"


@pytest.mark.parametrize("app_key", ["unknown_app", "RIO_COHERENCE", "admin"])
def test_unknown_app_is_404(client, ctx, app_key):
    response = client.get(f"/api/v1/apps/{app_key}/me", headers=ctx.h(ctx.superadmin))
    assert response.status_code == 404
    assert response.get_json()["error"] == "unknown_app"


def test_unknown_app_still_requires_authentication(client, ctx):
    assert client.get("/api/v1/apps/unknown_app/me").status_code == 401


def test_blocklisted_token_is_401(client, ctx):
    headers = ctx.h(ctx.member)
    raw = headers["Authorization"].removeprefix("Bearer ")
    claims = decode_token(raw, "access_token", audience=ctx.client.audience)
    add_token_to_blocklist(claims["jti"], 600)
    assert client.get(f"{ctx.base}/me", headers=headers).status_code == 401
    assert (
        client.patch(PROFILE_URL, json={"name": "X"}, headers=headers).status_code
        == 401
    )


def test_disabled_account_is_401_even_with_live_token(client, ctx):
    headers = ctx.h(ctx.member)
    ctx.member.is_active = False
    db.session.commit()
    assert client.get(f"{ctx.base}/me", headers=headers).status_code == 401
    assert (
        client.patch(PROFILE_URL, json={"name": "X"}, headers=headers).status_code
        == 401
    )


def test_unverified_email_is_rejected(client, ctx):
    user = _user("unverified@app.test", verified=False)
    _grant(user, ctx.key)
    headers = ctx.h(user)
    response = client.get(f"{ctx.base}/me", headers=headers)
    assert response.status_code == 403
    assert response.get_json()["error"] == "email_unverified"
    response = client.patch(PROFILE_URL, json={"name": "X"}, headers=headers)
    assert response.status_code == 403
    assert response.get_json()["error"] == "email_unverified"


@pytest.mark.parametrize("status", [None, STATUS_PENDING, STATUS_REVOKED])
def test_no_current_grant_is_403(client, ctx, status):
    user = _user(f"nogrant-{status}@app.test")
    if status:
        _grant(user, ctx.key, status=status)
    response = client.get(f"{ctx.base}/me", headers=ctx.h(user))
    assert response.status_code == 403
    body = response.get_json()
    assert body["error"] == "app_access_required"
    assert body["app_access"] == status


def test_grant_in_other_app_does_not_grant_this_app(client, ctx):
    user = _user("other-only@app.test")
    _grant(user, ctx.other)
    response = client.get(f"{ctx.base}/me", headers=ctx.h(user))
    assert response.status_code == 403
    assert response.get_json()["error"] == "app_access_required"


def test_global_admin_without_grant_is_not_app_admin(client, ctx, admin_user):
    User.query.filter_by(id=admin_user.id).update({"email_verified": True})
    db.session.commit()
    response = client.get(f"{ctx.base}/me", headers=ctx.h(admin_user))
    assert response.status_code == 403
    _grant(admin_user, ctx.key)
    response = client.get(f"{ctx.base}/users", headers=ctx.h(admin_user))
    assert response.status_code == 403
    assert response.get_json()["error"] == "app_role_required"


# ---------------------------------------------------------------------------
# Scopes: operation-specific, token grant AND client allowlist
# ---------------------------------------------------------------------------

OPERATIONS = [
    ("get", "/me", None, "app:read"),
    ("get", "/users", None, "app-users:read"),
    ("get", "/users?email=member@app.test", None, "app-users:read"),
    ("post", "/users/resolve", {"subjects": []}, "app-users:read"),
    ("put", "/users/{sub}/access", {"status": "active"}, "app-access:write"),
]


def _call(client, ctx, method, suffix, body, headers):
    url = f"{ctx.base}{suffix}".replace("{sub}", str(ctx.member.id))
    return getattr(client, method)(url, json=body, headers=headers)


@pytest.mark.parametrize("method,suffix,body,scope", OPERATIONS)
def test_operation_requires_its_scope_on_token(
    client, ctx, method, suffix, body, scope
):
    token_scopes = " ".join(s for s in ALL_SCOPES.split() if s != scope)
    response = _call(
        client, ctx, method, suffix, body, ctx.h(ctx.superadmin, token_scopes)
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "insufficient_scope"
    assert response.get_json()["missing_scopes"] == [scope]


@pytest.mark.parametrize("method,suffix,body,scope", OPERATIONS)
def test_operation_requires_its_scope_on_client_allowlist(
    client, ctx, method, suffix, body, scope
):
    headers = ctx.h(ctx.superadmin)
    ctx.client.scopes = " ".join(s for s in ALL_SCOPES.split() if s != scope)
    db.session.commit()
    response = _call(client, ctx, method, suffix, body, headers)
    assert response.status_code == 403
    assert response.get_json()["error"] == "insufficient_scope"


@pytest.mark.parametrize("missing", ["openid", "email", "profile"])
def test_base_scopes_are_required(client, ctx, missing):
    scopes = " ".join(s for s in ALL_SCOPES.split() if s != missing)
    response = client.get(f"{ctx.base}/me", headers=ctx.h(ctx.member, scopes))
    assert response.status_code == 403
    assert response.get_json()["error"] == "insufficient_scope"


def test_scope_does_not_confer_admin(client, ctx):
    response = client.get(f"{ctx.base}/users", headers=ctx.h(ctx.member))
    assert response.status_code == 403
    assert response.get_json()["error"] == "app_role_required"


def test_authorize_rejects_scopes_not_registered_for_client(client, ctx):
    base_only = _oauth_client("base-only", ctx.key, scopes=BASE_SCOPE)
    response = client.get(
        "/oauth/authorize",
        query_string={
            "client_id": base_only.client_id,
            "redirect_uri": "https://app.test/cb",
            "scope": "openid email profile app:read",
            "state": "s",
            "nonce": "n",
            "response_type": "code",
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_scope"


# ---------------------------------------------------------------------------
# Application profile (GET /me) and shared profile (PATCH /account/profile)
# ---------------------------------------------------------------------------


def test_get_me_returns_combined_profile(client, ctx):
    response = client.get(
        f"{ctx.base}/me", headers=ctx.h(ctx.member, BASE_SCOPE + " app:read")
    )
    assert response.status_code == 200
    assert response.get_json()["data"] == {
        "sub": str(ctx.member.id),
        "email": "member@app.test",
        "email_verified": True,
        "name": "App Member",
        "institution": "Inst",
        "active": True,
        "app_access": "active",
        "app_role": "member",
    }


def test_superadmin_is_effective_app_admin(client, ctx):
    _grant(ctx.superadmin, ctx.key, status=STATUS_REVOKED)
    response = client.get(f"{ctx.base}/me", headers=ctx.h(ctx.superadmin))
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert (data["app_access"], data["app_role"]) == ("active", "admin")


def test_no_app_scoped_profile_write_alias(client, ctx):
    response = client.patch(
        f"{ctx.base}/me", json={"name": "X"}, headers=ctx.h(ctx.member)
    )
    assert response.status_code in (404, 405)
    assert db.session.get(User, ctx.member.id).name == "App Member"


def test_account_profile_update_returns_identity_only(client, ctx):
    response = client.patch(
        PROFILE_URL,
        json={"name": "New Name", "institution": "New Inst"},
        headers=ctx.h(ctx.member, BASE_SCOPE + " account:write"),
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert set(data) == IDENTITY_FIELDS
    assert data["name"] == "New Name"
    assert data["institution"] == "New Inst"
    assert "app_access" not in data and "app_role" not in data
    assert db.session.get(User, ctx.member.id).name == "New Name"


def test_account_profile_requires_account_write_scope(client, ctx):
    response = client.patch(
        PROFILE_URL,
        json={"name": "New"},
        headers=ctx.h(ctx.member, BASE_SCOPE + " app:read"),
    )
    assert response.status_code == 403
    assert response.get_json()["missing_scopes"] == ["account:write"]
    headers = ctx.h(ctx.member)
    ctx.client.scopes = BASE_SCOPE + " app:read"
    db.session.commit()
    response = client.patch(PROFILE_URL, json={"name": "New"}, headers=headers)
    assert response.status_code == 403
    assert response.get_json()["error"] == "insufficient_scope"
    assert db.session.get(User, ctx.member.id).name == "App Member"


@pytest.mark.parametrize("status", [None, STATUS_PENDING, STATUS_REVOKED])
def test_account_profile_requires_current_grant_for_gated_client(client, ctx, status):
    user = _user("nogrant@app.test", name="Original")
    if status:
        _grant(user, ctx.key, status=status)
    response = client.patch(PROFILE_URL, json={"name": "New"}, headers=ctx.h(user))
    assert response.status_code == 403
    assert response.get_json()["error"] == "app_access_required"
    assert db.session.get(User, user.id).name == "Original"


def test_account_profile_with_ungated_client_needs_no_app_grant(client, ctx):
    ungated = _oauth_client("te-web", None, scopes="openid email profile account:write")
    user = _user("plain@app.test")
    response = client.patch(
        PROFILE_URL,
        json={"institution": ""},
        headers=_headers(user, ungated, BASE_SCOPE + " account:write"),
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["institution"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "SUPERADMIN"),
        ("email", "evil@app.test"),
        ("email_verified", True),
        ("app_role", "admin"),
        ("app_access", "active"),
        ("app_key", RIO),
        ("is_active", True),
        ("country", "FR"),
    ],
)
def test_account_profile_rejects_privileged_fields(client, ctx, field, value):
    response = client.patch(
        PROFILE_URL, json={"name": "Ok", field: value}, headers=ctx.h(ctx.member)
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "field_not_allowed"
    stored = db.session.get(User, ctx.member.id)
    assert (stored.name, stored.role) == ("App Member", "USER")


def test_account_profile_validates_input(client, ctx):
    headers = ctx.h(ctx.member)
    assert client.patch(PROFILE_URL, json={}, headers=headers).status_code == 400
    response = client.patch(PROFILE_URL, json={"name": "<script>"}, headers=headers)
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_profile"


# ---------------------------------------------------------------------------
# Discovery: exact-email lookup and subject resolution
# ---------------------------------------------------------------------------


def test_member_email_lookup_follows_discovery_policy(client, ctx):
    other = _user("Other@App.test", name="Never Signed In")
    _grant(other, ctx.key)
    response = client.get(
        f"{ctx.base}/users",
        query_string={"email": "other@app.TEST"},
        headers=ctx.h(ctx.member),
    )
    if ctx.policy.member_discovery:
        assert response.status_code == 200
        assert [i["sub"] for i in response.get_json()["data"]] == [str(other.id)]
    else:
        assert response.status_code == 403
        assert response.get_json()["error"] == "app_role_required"


def test_member_resolve_follows_discovery_policy(client, ctx):
    eligible = _user("eligible@app.test")
    _grant(eligible, ctx.key)
    pending = _user("pending@app.test")
    _grant(pending, ctx.key, status=STATUS_PENDING)
    unrelated = _user("unrelated@app.test")
    other_app = _user("other-app@app.test")
    _grant(other_app, ctx.other)
    disabled = _user("disabled@app.test", active=False)
    _grant(disabled, ctx.key)
    subjects = [
        str(eligible.id),
        str(pending.id),
        str(unrelated.id),
        str(other_app.id),
        str(disabled.id),
        str(ctx.superadmin.id),
        str(uuid.uuid4()),
        str(eligible.id),
    ]
    response = _resolve(client, ctx, ctx.h(ctx.member), subjects)
    if ctx.policy.member_discovery:
        assert response.status_code == 200
        assert [i["sub"] for i in response.get_json()["data"]] == [
            str(eligible.id),
            str(ctx.superadmin.id),
        ]
    else:
        assert response.status_code == 403
        assert response.get_json()["error"] == "app_role_required"


def test_discovery_policy_values():
    assert APP_POLICIES[RIO].member_discovery is True
    assert APP_POLICIES[AE].member_discovery is False


def test_admin_email_lookup_finds_never_signed_in_and_superadmin(client, ctx):
    other = _user("fresh@app.test")
    _grant(other, ctx.key)
    headers = ctx.h(ctx.admin)
    for email, expected in (
        ("fresh@app.test", other),
        ("root@app.test", ctx.superadmin),
    ):
        response = client.get(
            f"{ctx.base}/users", query_string={"email": email}, headers=headers
        )
        assert [i["sub"] for i in response.get_json()["data"]] == [str(expected.id)]


@pytest.mark.parametrize(
    "grant,verified,active,other_app",
    [
        (None, True, True, False),
        (STATUS_PENDING, True, True, False),
        (STATUS_REVOKED, True, True, False),
        (STATUS_ACTIVE, False, True, False),
        (STATUS_ACTIVE, True, False, False),
        (STATUS_ACTIVE, True, True, True),
    ],
)
def test_email_lookup_hides_ineligible_users(
    client, ctx, grant, verified, active, other_app
):
    target = _user("target@app.test", verified=verified, active=active)
    if grant:
        _grant(target, ctx.other if other_app else ctx.key, status=grant)
    response = client.get(
        f"{ctx.base}/users",
        query_string={"email": "target@app.test"},
        headers=ctx.h(ctx.admin),
    )
    assert response.status_code == 200
    assert response.get_json()["data"] == []


def test_email_lookup_requires_valid_email(client, ctx):
    response = client.get(
        f"{ctx.base}/users",
        query_string={"email": "not-an-email"},
        headers=ctx.h(ctx.admin),
    )
    assert response.status_code == 400


def test_admin_resolve_includes_pending_and_revoked_but_not_unrelated(client, ctx):
    pending = _user("pending@app.test")
    _grant(pending, ctx.key, status=STATUS_PENDING)
    revoked = _user("revoked@app.test")
    _grant(revoked, ctx.key, status=STATUS_REVOKED)
    unrelated = _user("unrelated@app.test")
    other_app = _user("other@app.test")
    _grant(other_app, ctx.other)
    response = _resolve(
        client,
        ctx,
        ctx.h(ctx.admin),
        [str(pending.id), str(revoked.id), str(unrelated.id), str(other_app.id)],
    )
    data = response.get_json()["data"]
    assert [i["sub"] for i in data] == [str(pending.id), str(revoked.id)]
    assert [i["app_access"] for i in data] == ["pending", "revoked"]


def test_resolve_is_bounded_and_validated(client, ctx):
    headers = ctx.h(ctx.admin)
    assert (
        _resolve(
            client, ctx, headers, [str(uuid.uuid4()) for _ in range(101)]
        ).status_code
        == 400
    )
    assert _resolve(client, ctx, headers, ["not-a-uuid"]).status_code == 400
    assert _resolve(client, ctx, headers, "nope").status_code == 400
    assert _resolve(client, ctx, headers, []).get_json() == {"data": []}


# ---------------------------------------------------------------------------
# Directory
# ---------------------------------------------------------------------------


def test_member_cannot_list_directory(client, ctx):
    response = client.get(f"{ctx.base}/users", headers=ctx.h(ctx.member))
    assert response.status_code == 403
    assert response.get_json()["error"] == "app_role_required"


def test_directory_includes_app_users_and_superadmins_only(client, ctx):
    pending = _user("pending@app.test")
    _grant(pending, ctx.key, status=STATUS_PENDING)
    revoked = _user("revoked@app.test")
    _grant(revoked, ctx.key, status=STATUS_REVOKED)
    other_only = _user("other-only@app.test")
    _grant(other_only, ctx.other)
    _user("unrelated@app.test")
    _user(ENVIRONMENT_USER_EMAIL)

    response = client.get(f"{ctx.base}/users", headers=ctx.h(ctx.admin))
    assert response.status_code == 200
    body = response.get_json()
    emails = {item["email"]: item for item in body["data"]}
    assert set(emails) == {
        "app-admin@app.test",
        "member@app.test",
        "root@app.test",
        "pending@app.test",
        "revoked@app.test",
        ENVIRONMENT_USER_EMAIL,
    }
    assert body["total"] == 6
    assert set(emails["member@app.test"]) == set(PROFILE_RESPONSE_FIELDS)
    assert emails["pending@app.test"]["app_access"] == STATUS_PENDING
    assert emails["pending@app.test"]["app_role"] is None
    assert emails["revoked@app.test"]["app_access"] == STATUS_REVOKED
    assert emails["root@app.test"]["app_role"] == "admin"
    assert emails[ENVIRONMENT_USER_EMAIL]["app_role"] == "admin"


def test_directory_status_filter(client, ctx):
    pending = _user("pending@app.test")
    _grant(pending, ctx.key, status=STATUS_PENDING)
    _grant(ctx.superadmin, ctx.key, status=STATUS_PENDING)
    headers = ctx.h(ctx.admin)
    response = client.get(
        f"{ctx.base}/users", query_string={"status": "pending"}, headers=headers
    )
    assert [i["email"] for i in response.get_json()["data"]] == ["pending@app.test"]
    response = client.get(
        f"{ctx.base}/users", query_string={"status": "active"}, headers=headers
    )
    assert {i["email"] for i in response.get_json()["data"]} == {
        "app-admin@app.test",
        "member@app.test",
        "root@app.test",
    }


def test_directory_pagination_is_bounded(client, ctx):
    for index in range(2):
        _grant(_user(f"user{index}@app.test"), ctx.key)
    headers = ctx.h(ctx.admin)
    body = client.get(
        f"{ctx.base}/users", query_string={"per_page": 2, "page": 2}, headers=headers
    ).get_json()
    assert (body["page"], body["per_page"], body["total"]) == (2, 2, 5)
    assert len(body["data"]) == 2
    for bad in (0, 101, "x"):
        response = client.get(
            f"{ctx.base}/users", query_string={"per_page": bad}, headers=headers
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Access writes
# ---------------------------------------------------------------------------


@pytest.fixture
def no_notifications():
    with patch.object(AppAccessService, "_dispatch_decision_notification"):
        yield


def test_admin_approves_pending_user(client, ctx, no_notifications):
    pending = _user("pending@app.test")
    _grant(pending, ctx.key, status=STATUS_PENDING)
    response = _put_access(
        client,
        ctx,
        ctx.h(ctx.admin, BASE_SCOPE + " app-access:write"),
        pending.id,
        {"status": "active", "role": "admin", "note": "ok"},
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert (data["app_access"], data["app_role"]) == ("active", "admin")
    grant = UserAppAccess.query.filter_by(user_id=pending.id, app_key=ctx.key).one()
    assert grant.granted_by_user_id == ctx.admin.id
    assert grant.note == "ok"


def test_admin_revokes_member(client, ctx, no_notifications):
    response = _put_access(
        client, ctx, ctx.h(ctx.admin), ctx.member.id, {"status": "revoked"}
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert (data["app_access"], data["app_role"]) == ("revoked", None)


def test_member_cannot_write_access(client, ctx):
    other = _user("other@app.test")
    _grant(other, ctx.key, status=STATUS_PENDING)
    response = _put_access(
        client, ctx, ctx.h(ctx.member), other.id, {"status": "active"}
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "app_role_required"
    grant = UserAppAccess.query.filter_by(user_id=other.id, app_key=ctx.key).one()
    assert grant.status == STATUS_PENDING


def test_write_only_touches_this_apps_grant(client, ctx, no_notifications):
    other = _user("other@app.test")
    _grant(other, ctx.other)
    _put_access(client, ctx, ctx.h(ctx.admin), other.id, {"status": "revoked"})
    assert (
        UserAppAccess.query.filter_by(user_id=other.id, app_key=ctx.other).one().status
        == STATUS_ACTIVE
    )


def test_admin_of_other_app_cannot_write_here(client, ctx):
    other_admin = _user("other-admin@app.test")
    _grant(other_admin, ctx.other, role="admin")
    _grant(other_admin, ctx.key)
    response = _put_access(
        client, ctx, ctx.h(other_admin), ctx.member.id, {"status": "revoked"}
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "app_role_required"


@pytest.mark.parametrize(
    "body",
    [
        {"status": "revoked"},
        {"status": "pending"},
        {"status": "active", "role": "member"},
    ],
)
def test_effective_superadmin_cannot_be_revoked_or_demoted(client, ctx, body):
    response = _put_access(client, ctx, ctx.h(ctx.admin), ctx.superadmin.id, body)
    assert response.status_code == 403
    assert response.get_json()["error"] == "superadmin_protected"
    assert UserAppAccess.query.filter_by(user_id=ctx.superadmin.id).count() == 0


@pytest.mark.parametrize(
    "body",
    [
        {"status": "revoked"},
        {"status": "pending"},
        {"status": "active", "role": "member"},
    ],
)
def test_admin_cannot_demote_or_revoke_self(client, ctx, body):
    response = _put_access(client, ctx, ctx.h(ctx.admin), ctx.admin.id, body)
    assert response.status_code == 409
    assert response.get_json()["error"] == "self_demotion_forbidden"
    grant = UserAppAccess.query.filter_by(user_id=ctx.admin.id, app_key=ctx.key).one()
    assert (grant.status, grant.role) == (STATUS_ACTIVE, "admin")


@pytest.mark.parametrize(
    "body",
    [
        {"status": "bogus"},
        {"status": "active", "role": "owner"},
        {"status": "active", "app_key": RIO},
        {"status": "active", "note": 5},
        {},
    ],
)
def test_access_write_validates_body(client, ctx, body):
    response = _put_access(client, ctx, ctx.h(ctx.admin), ctx.member.id, body)
    assert response.status_code == 400


def test_access_write_unknown_subject(client, ctx):
    headers = ctx.h(ctx.admin)
    assert (
        _put_access(
            client, ctx, headers, uuid.uuid4(), {"status": "active"}
        ).status_code
        == 404
    )
    assert (
        _put_access(
            client, ctx, headers, "not-a-uuid", {"status": "active"}
        ).status_code
        == 400
    )


def test_stale_admin_token_reflects_live_role_and_grant_changes(
    client, ctx, no_notifications
):
    stale = ctx.h(ctx.admin)
    super_headers = ctx.h(ctx.superadmin)
    assert (
        client.get(f"{ctx.base}/me", headers=stale).get_json()["data"]["app_role"]
        == "admin"
    )
    assert client.get(f"{ctx.base}/users", headers=stale).status_code == 200

    demote = {"status": "active", "role": "member"}
    assert (
        _put_access(client, ctx, super_headers, ctx.admin.id, demote).status_code == 200
    )
    me = client.get(f"{ctx.base}/me", headers=stale)
    assert me.status_code == 200
    assert me.get_json()["data"]["app_role"] == "member"
    assert client.get(f"{ctx.base}/users", headers=stale).status_code == 403
    denied = _put_access(client, ctx, stale, ctx.member.id, {"status": "revoked"})
    assert denied.status_code == 403
    assert denied.get_json()["error"] == "app_role_required"

    assert (
        _put_access(
            client, ctx, super_headers, ctx.admin.id, {"status": "revoked"}
        ).status_code
        == 200
    )
    me = client.get(f"{ctx.base}/me", headers=stale)
    assert me.status_code == 403
    assert me.get_json()["app_access"] == "revoked"
    assert (
        UserAppAccess.query.filter_by(user_id=ctx.member.id, app_key=ctx.key)
        .one()
        .status
        == STATUS_ACTIVE
    )


# ---------------------------------------------------------------------------
# Registry policy (explicit, fail closed)
# ---------------------------------------------------------------------------


def _policy(**overrides):
    values = {
        "label": "Custom",
        "roles": ("viewer", "owner", "admin"),
        "default_role": "viewer",
        "admin_roles": frozenset({"owner", "admin"}),
        "superadmin_role": "admin",
        "member_discovery": False,
        "directory_fields": ("sub", "email", "app_role"),
    }
    values.update(overrides)
    return AppPolicy(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"roles": ()},
        {"default_role": "guest"},
        {"default_role": "owner"},
        {"admin_roles": frozenset()},
        {"admin_roles": frozenset({"root"})},
        {"superadmin_role": "viewer"},
        {"member_discovery": None},
        {"directory_fields": ("email",)},
        {"directory_fields": ("sub", "password")},
    ],
)
def test_invalid_policies_fail_closed(overrides):
    with pytest.raises((ValueError, TypeError)):
        _policy(**overrides)


def test_custom_role_policy_is_enforced(client, app, no_notifications):
    custom = _policy()
    with patch.dict(APP_POLICIES, {RIO: custom}):
        oauth = _oauth_client("custom-web", RIO)
        owner = _user("owner@app.test")
        _grant(owner, RIO, role="owner")
        viewer = _user("viewer@app.test")
        _grant(viewer, RIO, role="viewer")
        base = f"/api/v1/apps/{RIO}"
        owner_h = _headers(owner, oauth)
        viewer_h = _headers(viewer, oauth)

        directory = client.get(f"{base}/users", headers=owner_h)
        assert directory.status_code == 200
        assert all(
            set(item) == {"sub", "email", "app_role"}
            for item in directory.get_json()["data"]
        )

        lookup = client.get(
            f"{base}/users", query_string={"email": "owner@app.test"}, headers=viewer_h
        )
        assert lookup.status_code == 403

        promote = client.put(
            f"{base}/users/{viewer.id}/access",
            json={"status": "active", "role": "owner"},
            headers=owner_h,
        )
        assert promote.status_code == 200
        assert promote.get_json()["data"]["app_role"] == "owner"
        assert client.get(f"{base}/users", headers=viewer_h).status_code == 200

        bad_role = client.put(
            f"{base}/users/{viewer.id}/access",
            json={"status": "active", "role": "member"},
            headers=owner_h,
        )
        assert bad_role.status_code == 400

        self_demote = client.put(
            f"{base}/users/{owner.id}/access",
            json={"status": "active", "role": "viewer"},
            headers=owner_h,
        )
        assert self_demote.status_code == 409


# ---------------------------------------------------------------------------
# Client registration defaults
# ---------------------------------------------------------------------------


def _client_manage_headers(user):
    token = create_access_token(
        identity=user.id,
        additional_claims={
            "grant_type": "client_credentials",
            "scopes": "client:manage",
        },
    )
    return {"Authorization": f"Bearer {token}"}


def _register(client, admin_user, **fields):
    body = {
        "client_id": fields.pop("client_id", "new-client"),
        "name": "New",
        "redirect_uris": ["https://new.test/cb"],
        "audience": "new-aud",
    }
    body.update(fields)
    return client.post(
        "/api/v1/admin/oidc-clients",
        json=body,
        headers=_client_manage_headers(admin_user),
    )


@pytest.mark.parametrize(
    "app_key,expected",
    [(RIO, ALL_SCOPES.split()), (AE, ALL_SCOPES.split()), (None, BASE_SCOPE.split())],
)
def test_registration_default_scopes(client, admin_user, app_key, expected):
    fields = {"required_app_key": app_key} if app_key else {}
    response = _register(client, admin_user, **fields)
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["data"]["scopes"] == expected


@pytest.mark.parametrize(
    "app_key,scopes",
    [
        (None, ["openid", "app:read"]),
        (None, ["openid", "app-access:write"]),
        (RIO, ["openid", "offline_access"]),
        (RIO, ["email", "profile", "app:read"]),
    ],
)
def test_registration_rejects_invalid_scopes(client, admin_user, app_key, scopes):
    fields = {"scopes": scopes}
    if app_key:
        fields["required_app_key"] = app_key
    response = _register(client, admin_user, **fields)
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_scope"


def test_discovery_advertises_new_scopes(client):
    supported = client.get("/.well-known/openid-configuration").get_json()[
        "scopes_supported"
    ]
    assert set(ALL_SCOPES.split()) <= set(supported)


# ---------------------------------------------------------------------------
# Authorize denial redirect
# ---------------------------------------------------------------------------


def _authorize(client_app, oauth_client):
    return client_app.get(
        "/oauth/authorize",
        query_string={
            "client_id": oauth_client.client_id,
            "redirect_uri": "https://app.test/cb",
            "scope": "openid",
            "state": "state-xyz",
            "nonce": "nonce",
            "response_type": "code",
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
        },
    )


def _signed_in(client_app, user):
    with client_app.session_transaction() as session:
        session["oidc_user_id"] = str(user.id)
        session["oidc_auth_version"] = user.auth_version


def test_denial_reports_unavailable_when_request_cannot_be_persisted(client, ctx):
    user = _user("newcomer@app.test")
    _signed_in(client, user)
    with patch.object(
        AppAccessService, "request_access", side_effect=RuntimeError("db down")
    ):
        response = _authorize(client, ctx.client)
    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.startswith("https://app.test/cb?")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    assert params["error"] == ["access_denied"]
    assert params["te_app_access"] == ["unavailable"]
    assert params["te_app_key"] == [ctx.key]
    assert params["state"] == ["state-xyz"]
    assert "code" not in params
    assert UserAppAccess.query.filter_by(user_id=user.id).count() == 0


def test_denial_reports_pending_only_after_persisting(client, ctx):
    user = _user("newcomer@app.test")
    _signed_in(client, user)
    with patch.object(AppAccessService, "_dispatch_request_notification"):
        response = _authorize(client, ctx.client)
    params = urllib.parse.parse_qs(
        urllib.parse.urlparse(response.headers["Location"]).query
    )
    assert params["te_app_access"] == ["pending"]
    grant = UserAppAccess.query.filter_by(user_id=user.id, app_key=ctx.key).one()
    assert grant.status == STATUS_PENDING
