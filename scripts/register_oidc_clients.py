"""Register or update the first-party OIDC clients.

Idempotent: safe to re-run.  Existing clients are updated in place; their
``client_id`` and ``audience`` are never changed, because live access tokens
resolve their client by those values.

Usage (from the admin container)::

    python scripts/register_oidc_clients.py
    python scripts/register_oidc_clients.py --client rio-coherence
    python scripts/register_oidc_clients.py --rotate-secret avoided-emissions-web
    python scripts/register_oidc_clients.py --client rio-coherence --add-scopes
    python scripts/register_oidc_clients.py --client rio-coherence --dry-run

New clients are created with their definition's scopes.  Existing clients'
scope allowlists are never changed automatically: the script reports any
missing definition scopes, and ``--add-scopes`` adds them (it never removes a
scope).  ``--dry-run`` reports what would change without writing anything.
Application-bound clients default to ``openid email profile`` plus
``app:read account:write app-users:read app-access:write``; ungated clients
get ``openid email profile`` only.  App scopes must be provisioned before an
application requests them.

Redirect URIs come from environment variables so that staging and production
can be provisioned with the same script:

    API_UI_REDIRECT_URIS,  API_UI_POST_LOGOUT_REDIRECT_URIS,
    TE_WEB_REDIRECT_URIS,
    TE_WEB_POST_LOGOUT_REDIRECT_URIS,
    AVOIDED_EMISSIONS_REDIRECT_URIS,
    AVOIDED_EMISSIONS_POST_LOGOUT_REDIRECT_URIS,
    RIO_REDIRECT_URIS, RIO_POST_LOGOUT_REDIRECT_URIS,
    RIO_STAGING_REDIRECT_URIS, RIO_STAGING_POST_LOGOUT_REDIRECT_URIS

``rio-coherence-staging`` serves staging and local Rio instances.  It is gated
by its own ``rio_coherence_staging`` app key, so testers sign in with their
existing accounts but need a separate staging grant.

The logo shown on the hosted sign-in/register pages can be set per client with
API_UI_LOGO_URL, TE_WEB_LOGO_URL, AVOIDED_EMISSIONS_LOGO_URL, RIO_LOGO_URL, or
RIO_STAGING_LOGO_URL
(an https URL or a /static/ path).  When unset, an existing logo is kept.
"""

import argparse
import os
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gefapi import app, db
from gefapi.models import OAuthClient
from gefapi.services.oidc_service import TRENDS_API_AUDIENCE, valid_logo_url
from gefapi.utils.app_access import (
    APP_AVOIDED_EMISSIONS,
    APP_RIO_COHERENCE,
    APP_RIO_COHERENCE_STAGING,
)
from gefapi.utils.oidc_scopes import (
    DEFAULT_APP_CLIENT_SCOPES,
    DEFAULT_CLIENT_SCOPES,
    client_scope_error,
    default_client_scopes,
)


def _uris(env_var, fallback):
    raw = os.getenv(env_var, "")
    values = [item.strip() for item in raw.replace(",", "\n").split("\n")]
    values = [item for item in values if item]
    if values:
        return values
    if os.getenv("ENVIRONMENT", "dev").casefold() in {"staging", "prod"}:
        return []
    return list(fallback or [])


def client_definitions():
    return [
        {
            "client_id": "te-api-ui",
            "name": "Trends.Earth API UI",
            "audience": "trends-earth-api-ui",
            "is_public": True,
            "required_app_key": None,
            "scopes": list(DEFAULT_CLIENT_SCOPES),
            "logo_url": os.getenv("API_UI_LOGO_URL"),
            "redirect_uris": _uris(
                "API_UI_REDIRECT_URIS", ["http://localhost:8050/auth/callback"]
            ),
            "post_logout_redirect_uris": _uris(
                "API_UI_POST_LOGOUT_REDIRECT_URIS", ["http://localhost:8050/"]
            ),
        },
        {
            "client_id": "te-qgis-plugin",
            "name": "Trends.Earth QGIS Plugin",
            "audience": TRENDS_API_AUDIENCE,
            "is_public": True,
            "required_app_key": None,
            "scopes": list(DEFAULT_CLIENT_SCOPES),
            # RFC 8252 loopback: the port is wildcarded at match time.
            "redirect_uris": ["http://127.0.0.1/callback", "http://[::1]/callback"],
            "post_logout_redirect_uris": [],
        },
        {
            "client_id": "te-web",
            "name": "Trends.Earth Web",
            "audience": TRENDS_API_AUDIENCE,
            "is_public": True,
            "required_app_key": None,
            "scopes": list(DEFAULT_CLIENT_SCOPES),
            "logo_url": os.getenv("TE_WEB_LOGO_URL"),
            "redirect_uris": _uris("TE_WEB_REDIRECT_URIS", []),
            "post_logout_redirect_uris": _uris("TE_WEB_POST_LOGOUT_REDIRECT_URIS", []),
        },
        {
            "client_id": "avoided-emissions-web",
            "name": "Avoided Emissions Webapp",
            "audience": "avoided-emissions",
            "is_public": False,
            "required_app_key": APP_AVOIDED_EMISSIONS,
            "scopes": list(DEFAULT_APP_CLIENT_SCOPES),
            "logo_url": os.getenv("AVOIDED_EMISSIONS_LOGO_URL"),
            "redirect_uris": _uris(
                "AVOIDED_EMISSIONS_REDIRECT_URIS",
                ["http://localhost:8051/auth/callback"],
            ),
            "post_logout_redirect_uris": _uris(
                "AVOIDED_EMISSIONS_POST_LOGOUT_REDIRECT_URIS",
                ["http://localhost:8051/"],
            ),
        },
        {
            "client_id": "rio-coherence",
            "name": "Rio Coherence",
            "audience": "rio-coherence",
            "is_public": True,
            "required_app_key": APP_RIO_COHERENCE,
            "scopes": list(DEFAULT_APP_CLIENT_SCOPES),
            "logo_url": os.getenv("RIO_LOGO_URL"),
            "redirect_uris": _uris(
                "RIO_REDIRECT_URIS", ["http://localhost:3000/auth/callback"]
            ),
            "post_logout_redirect_uris": _uris(
                "RIO_POST_LOGOUT_REDIRECT_URIS", ["http://localhost:3000/"]
            ),
        },
        {
            # Staging and local Rio instances; separate grants from production Rio.
            "client_id": "rio-coherence-staging",
            "name": "Rio Coherence (staging)",
            "audience": "rio-coherence-staging",
            "is_public": True,
            "required_app_key": APP_RIO_COHERENCE_STAGING,
            "scopes": list(DEFAULT_APP_CLIENT_SCOPES),
            "logo_url": os.getenv("RIO_STAGING_LOGO_URL"),
            "redirect_uris": _uris(
                "RIO_STAGING_REDIRECT_URIS", ["http://localhost:3000/auth/callback"]
            ),
            "post_logout_redirect_uris": _uris(
                "RIO_STAGING_POST_LOGOUT_REDIRECT_URIS", ["http://localhost:3000/"]
            ),
        },
    ]


def definition_scopes(definition):
    scopes = definition.get("scopes") or default_client_scopes(
        definition["required_app_key"]
    )
    error = client_scope_error(scopes, definition["required_app_key"])
    if error:
        raise SystemExit(f"Invalid scopes for {definition['client_id']}: {error}")
    return list(scopes)


def missing_scopes(client, definition):
    registered = set((client.scopes or "").split())
    return [scope for scope in definition_scopes(definition) if scope not in registered]


def upsert_client(definition, rotate_secret=False, add_scopes=False, dry_run=False):
    """Create or update one client.

    Returns ``(action, secret, scopes, missing)`` where ``scopes`` is the
    resulting allowlist and ``missing`` lists definition scopes the client
    still lacks.  With ``dry_run`` nothing is written and no secret is issued.
    """
    from werkzeug.security import generate_password_hash

    scopes = definition_scopes(definition)
    if not definition["redirect_uris"]:
        raise SystemExit(
            f"No redirect URI configured for {definition['client_id']}; "
            "set its *_REDIRECT_URIS environment variable before registration."
        )
    logo_url = definition.get("logo_url")
    if logo_url and not valid_logo_url(logo_url):
        raise SystemExit(
            f"Invalid logo URL for {definition['client_id']}: "
            "use an https URL or a /static/ path."
        )

    existing = OAuthClient.query.filter_by(
        client_id=definition["client_id"]
    ).one_or_none()
    secret = None

    if existing is None:
        if dry_run:
            return "would create", None, scopes, []
        if not definition["is_public"]:
            secret = secrets.token_urlsafe(32)
        existing = OAuthClient(
            client_id=definition["client_id"],
            name=definition["name"],
            audience=definition["audience"],
            redirect_uris="\n".join(definition["redirect_uris"]),
            post_logout_redirect_uris="\n".join(
                definition["post_logout_redirect_uris"]
            ),
            scopes=" ".join(scopes),
            is_public=definition["is_public"],
            is_active=True,
            required_app_key=definition["required_app_key"],
            logo_url=logo_url or None,
            client_secret_hash=generate_password_hash(secret) if secret else None,
        )
        db.session.add(existing)
        db.session.commit()
        return "created", secret, scopes, []

    registered = (existing.scopes or "").split()
    missing = missing_scopes(existing, definition)
    if add_scopes and missing:
        resulting = [*registered, *missing]
        remaining = []
    else:
        resulting = registered
        remaining = missing
    if dry_run:
        return "would update", None, resulting, remaining

    # client_id and audience are deliberately never modified.
    existing.name = definition["name"]
    existing.redirect_uris = "\n".join(definition["redirect_uris"])
    existing.post_logout_redirect_uris = "\n".join(
        definition["post_logout_redirect_uris"]
    )
    existing.is_public = definition["is_public"]
    existing.required_app_key = definition["required_app_key"]
    if logo_url:
        existing.logo_url = logo_url
    existing.is_active = True
    existing.scopes = " ".join(resulting)
    if rotate_secret and not definition["is_public"]:
        secret = secrets.token_urlsafe(32)
        existing.client_secret_hash = generate_password_hash(secret)
    db.session.commit()
    return "updated", secret, resulting, remaining


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", help="Only provision this client_id")
    parser.add_argument(
        "--add-scopes",
        action="store_true",
        help="Add missing definition scopes to existing clients (never removes)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing anything",
    )
    parser.add_argument(
        "--rotate-secret",
        metavar="CLIENT_ID",
        help="Issue a new secret for a confidential client",
    )
    args = parser.parse_args(argv)
    definitions = {d["client_id"]: d for d in client_definitions()}
    if args.client and args.client not in definitions:
        parser.error(
            f"unknown client '{args.client}'; expected one of: "
            + ", ".join(sorted(definitions))
        )
    if args.rotate_secret:
        target = definitions.get(args.rotate_secret)
        if target is None or target["is_public"]:
            parser.error(
                f"--rotate-secret requires a confidential client_id, "
                f"got '{args.rotate_secret}'"
            )
        if args.client and args.client != args.rotate_secret:
            parser.error("--rotate-secret must match --client when both are given")
        if args.dry_run:
            parser.error("--rotate-secret cannot be combined with --dry-run")
    return args


def main(argv=None):
    args = parse_args(argv)

    with app.app_context():
        for definition in client_definitions():
            if args.client and definition["client_id"] != args.client:
                continue
            action, secret, scopes, missing = upsert_client(
                definition,
                rotate_secret=args.rotate_secret == definition["client_id"],
                add_scopes=args.add_scopes,
                dry_run=args.dry_run,
            )
            gate = definition["required_app_key"] or "ungated"
            print(f"{action}: {definition['client_id']} ({gate})")
            print(f"  scopes: {' '.join(scopes)}")
            if missing:
                print(
                    f"  missing scopes (not changed): {' '.join(missing)}; "
                    "re-run with --add-scopes to provision them"
                )
            if secret:
                print(
                    f"  client_secret: {secret}\n"
                    "  Store this now - it is not recoverable."
                )


if __name__ == "__main__":
    main()
