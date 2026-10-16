"""Tests for scripts/register_oidc_clients.py scope provisioning."""

import importlib.util
from pathlib import Path

import pytest

from gefapi import db
from gefapi.models import OAuthClient
from gefapi.utils.oidc_scopes import DEFAULT_APP_CLIENT_SCOPES, DEFAULT_CLIENT_SCOPES

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "register_oidc_clients.py"

BASE = ["openid", "email", "profile"]
APP_SCOPES = ["app:read", "account:write", "app-users:read", "app-access:write"]
GATED = ["rio-coherence", "avoided-emissions-web"]
UNGATED = ["te-api-ui", "te-qgis-plugin", "te-web"]


@pytest.fixture(scope="module")
def script():
    if not SCRIPT.exists():
        pytest.skip("scripts/ is not available in this environment")
    spec = importlib.util.spec_from_file_location("register_oidc_clients", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "parse_args"):
        # The test image bakes in a copy of the script at build time.
        pytest.skip(
            "stale scripts/register_oidc_clients.py in image; rebuild the image "
            "or mount ./scripts:/opt/gef-api/scripts"
        )
    return module


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("TE_WEB_REDIRECT_URIS", "https://web.example.org/callback")
    for var in (
        "API_UI_LOGO_URL",
        "TE_WEB_LOGO_URL",
        "AVOIDED_EMISSIONS_LOGO_URL",
        "RIO_LOGO_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def _definition(script, client_id):
    return next(d for d in script.client_definitions() if d["client_id"] == client_id)


def _client(client_id):
    db.session.expire_all()
    return OAuthClient.query.filter_by(client_id=client_id).one_or_none()


def _seed(script, client_id, scopes, audience=None):
    definition = _definition(script, client_id)
    client = OAuthClient(
        client_id=client_id,
        name=definition["name"],
        audience=audience or definition["audience"],
        redirect_uris="\n".join(definition["redirect_uris"]),
        post_logout_redirect_uris="",
        scopes=" ".join(scopes),
        is_public=definition["is_public"],
        is_active=True,
        required_app_key=definition["required_app_key"],
    )
    db.session.add(client)
    db.session.commit()
    return client


def test_presets_are_explicit(script, env):
    assert list(DEFAULT_CLIENT_SCOPES) == BASE
    assert list(DEFAULT_APP_CLIENT_SCOPES) == BASE + APP_SCOPES
    for definition in script.client_definitions():
        expected = BASE + APP_SCOPES if definition["required_app_key"] else BASE
        assert definition["scopes"] == expected, definition["client_id"]
        assert script.definition_scopes(definition) == expected
    gated = {
        d["client_id"] for d in script.client_definitions() if d["required_app_key"]
    }
    assert gated == set(GATED)


@pytest.mark.parametrize("client_id", GATED + UNGATED)
def test_new_client_created_with_preset(app, script, env, client_id):
    action, _, scopes, missing = script.upsert_client(_definition(script, client_id))
    assert action == "created"
    assert missing == []
    expected = BASE + APP_SCOPES if client_id in GATED else BASE
    assert scopes == expected
    assert _client(client_id).scopes.split() == expected


@pytest.mark.parametrize("client_id", GATED)
def test_existing_client_scopes_unchanged_without_flag(app, script, env, client_id):
    _seed(script, client_id, BASE)
    action, _, scopes, missing = script.upsert_client(_definition(script, client_id))
    assert action == "updated"
    assert scopes == BASE
    assert missing == APP_SCOPES
    assert _client(client_id).scopes.split() == BASE


@pytest.mark.parametrize("client_id", GATED)
def test_add_scopes_unions_and_preserves(app, script, env, client_id):
    prior = [*BASE, "app:read", "legacy:custom"]
    _seed(script, client_id, prior, audience="original-audience")
    action, _, scopes, missing = script.upsert_client(
        _definition(script, client_id), add_scopes=True
    )
    assert action == "updated"
    assert missing == []
    client = _client(client_id)
    assert client.scopes.split() == [
        *prior,
        "account:write",
        "app-users:read",
        "app-access:write",
    ]
    assert scopes == client.scopes.split()
    assert client.audience == "original-audience"
    assert client.client_id == client_id

    # Idempotent: a second run adds nothing more.
    script.upsert_client(_definition(script, client_id), add_scopes=True)
    assert _client(client_id).scopes.split() == scopes


def test_add_scopes_never_broadens_ungated(app, script, env):
    _seed(script, "te-web", ["openid"])
    _, _, scopes, missing = script.upsert_client(
        _definition(script, "te-web"), add_scopes=True
    )
    assert scopes == BASE
    assert missing == []
    assert _client("te-web").scopes.split() == BASE


@pytest.mark.parametrize("add_scopes", [False, True])
def test_dry_run_never_mutates_existing(app, script, env, add_scopes):
    _seed(script, "rio-coherence", BASE, audience="original-audience")
    before = _client("rio-coherence")
    snapshot = {
        column: getattr(before, column)
        for column in (
            "name",
            "audience",
            "redirect_uris",
            "post_logout_redirect_uris",
            "scopes",
            "is_public",
            "is_active",
            "required_app_key",
            "logo_url",
            "client_secret_hash",
        )
    }
    action, secret, scopes, missing = script.upsert_client(
        _definition(script, "rio-coherence"), add_scopes=add_scopes, dry_run=True
    )
    assert action == "would update"
    assert secret is None
    if add_scopes:
        assert scopes == BASE + APP_SCOPES
        assert missing == []
    else:
        assert scopes == BASE
        assert missing == APP_SCOPES
    after = _client("rio-coherence")
    assert {column: getattr(after, column) for column in snapshot} == snapshot


def test_dry_run_never_creates(app, script, env):
    action, secret, scopes, _ = script.upsert_client(
        _definition(script, "avoided-emissions-web"), dry_run=True
    )
    assert action == "would create"
    assert secret is None
    assert scopes == BASE + APP_SCOPES
    assert _client("avoided-emissions-web") is None


def test_main_dry_run_reports_without_writing(app, script, env, capsys):
    _seed(script, "rio-coherence", BASE)
    script.main(["--client", "rio-coherence", "--add-scopes", "--dry-run"])
    out = capsys.readouterr().out
    assert "would update: rio-coherence (rio_coherence)" in out
    assert "scopes: " + " ".join(BASE + APP_SCOPES) in out
    assert _client("rio-coherence").scopes.split() == BASE
    assert OAuthClient.query.count() == 1


def test_main_reports_missing_without_flag(app, script, env, capsys):
    _seed(script, "rio-coherence", BASE)
    script.main(["--client", "rio-coherence"])
    out = capsys.readouterr().out
    assert "missing scopes (not changed): " + " ".join(APP_SCOPES) in out
    assert "--add-scopes" in out
    assert _client("rio-coherence").scopes.split() == BASE


@pytest.mark.parametrize(
    "argv",
    [
        ["--client", "no-such-client"],
        ["--rotate-secret", "no-such-client"],
        ["--rotate-secret", "rio-coherence"],
        ["--rotate-secret", "avoided-emissions-web", "--client", "rio-coherence"],
        ["--rotate-secret", "avoided-emissions-web", "--dry-run"],
        ["--unknown-flag"],
    ],
)
def test_invalid_arguments_rejected(app, script, env, capsys, argv):
    with pytest.raises(SystemExit) as excinfo:
        script.main(argv)
    assert excinfo.value.code == 2
    assert "error:" in capsys.readouterr().err
    assert OAuthClient.query.count() == 0


def test_invalid_definition_scopes_fail_closed(app, script, env):
    definition = dict(_definition(script, "te-web"), scopes=[*BASE, "app:read"])
    with pytest.raises(SystemExit, match="Invalid scopes for te-web"):
        script.upsert_client(definition)
    definition = dict(_definition(script, "rio-coherence"), scopes=["email"])
    with pytest.raises(SystemExit, match="Invalid scopes for rio-coherence"):
        script.upsert_client(definition)
    assert OAuthClient.query.count() == 0
