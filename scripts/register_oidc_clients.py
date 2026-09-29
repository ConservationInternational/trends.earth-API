"""Register or update the first-party OIDC clients.

Idempotent: safe to re-run.  Existing clients are updated in place; their
``client_id`` and ``audience`` are never changed, because live access tokens
resolve their client by those values.

Usage (from the admin container)::

    python scripts/register_oidc_clients.py
    python scripts/register_oidc_clients.py --client rio-coherence
    python scripts/register_oidc_clients.py --rotate-secret avoided-emissions-web

Redirect URIs come from environment variables so that staging and production
can be provisioned with the same script:

    API_UI_REDIRECT_URIS,  API_UI_POST_LOGOUT_REDIRECT_URIS,
    TE_WEB_REDIRECT_URIS,
    TE_WEB_POST_LOGOUT_REDIRECT_URIS,
    AVOIDED_EMISSIONS_REDIRECT_URIS,
    AVOIDED_EMISSIONS_POST_LOGOUT_REDIRECT_URIS,
    RIO_REDIRECT_URIS, RIO_POST_LOGOUT_REDIRECT_URIS

The logo shown on the hosted sign-in/register pages can be set per client with
API_UI_LOGO_URL, TE_WEB_LOGO_URL, AVOIDED_EMISSIONS_LOGO_URL, or RIO_LOGO_URL
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
from gefapi.utils.app_access import APP_AVOIDED_EMISSIONS, APP_RIO_COHERENCE

DEFAULT_SCOPES = ["openid", "email", "profile"]


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
            "logo_url": os.getenv("RIO_LOGO_URL"),
            "redirect_uris": _uris(
                "RIO_REDIRECT_URIS", ["http://localhost:3000/auth/callback"]
            ),
            "post_logout_redirect_uris": _uris(
                "RIO_POST_LOGOUT_REDIRECT_URIS", ["http://localhost:3000/"]
            ),
        },
    ]


def upsert_client(definition, rotate_secret=False):
    from werkzeug.security import generate_password_hash

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
            scopes=" ".join(DEFAULT_SCOPES),
            is_public=definition["is_public"],
            is_active=True,
            required_app_key=definition["required_app_key"],
            logo_url=logo_url or None,
            client_secret_hash=generate_password_hash(secret) if secret else None,
        )
        db.session.add(existing)
        action = "created"
    else:
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
        if rotate_secret and not definition["is_public"]:
            secret = secrets.token_urlsafe(32)
            existing.client_secret_hash = generate_password_hash(secret)
        action = "updated"

    db.session.commit()
    return action, secret


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", help="Only provision this client_id")
    parser.add_argument(
        "--rotate-secret",
        metavar="CLIENT_ID",
        help="Issue a new secret for a confidential client",
    )
    args = parser.parse_args()

    with app.app_context():
        for definition in client_definitions():
            if args.client and definition["client_id"] != args.client:
                continue
            action, secret = upsert_client(
                definition, rotate_secret=args.rotate_secret == definition["client_id"]
            )
            gate = definition["required_app_key"] or "ungated"
            print(f"{action}: {definition['client_id']} ({gate})")
            if secret:
                print(
                    f"  client_secret: {secret}\n"
                    "  Store this now - it is not recoverable."
                )


if __name__ == "__main__":
    main()
