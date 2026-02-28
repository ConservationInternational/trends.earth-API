"""OAUTH2 SERVICE

Business logic for creating, listing, revoking, and authenticating
OAuth2 service clients using the Client Credentials grant (RFC 6749 §4.4).
"""

import datetime
import logging

import rollbar

from gefapi import db
from gefapi.errors import AuthError, NotAllowed
from gefapi.models.service_client import (
    CLIENT_SECRET_PREFIX,
    ServiceClient,
)

logger = logging.getLogger(__name__)

MAX_CLIENTS_PER_USER = 10


class OAuth2Service:
    """Manages OAuth2 service client lifecycle and authentication."""

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    @staticmethod
    def create_client(user, name, scopes="", expires_in_days=None):
        """Create a new service client and return ``(raw_secret, client)``.

        The ``raw_secret`` is returned **once** — it cannot be retrieved
        later.

        Parameters
        ----------
        user : User
            Owner whose permissions the client inherits.
        name : str
            Human-readable label.
        scopes : str
            Space-delimited scope list (empty = full user access).
        expires_in_days : int | None
            Optional lifetime.  ``None`` means no expiry.

        Returns
        -------
        tuple[str, ServiceClient]
        """
        active_count = ServiceClient.query.filter_by(
            user_id=user.id, revoked=False
        ).count()
        if active_count >= MAX_CLIENTS_PER_USER:
            raise NotAllowed(
                message=(
                    f"Maximum of {MAX_CLIENTS_PER_USER} active service "
                    "clients reached. Revoke an existing client first."
                )
            )

        client_id, raw_secret, secret_hash = ServiceClient.generate_credentials()

        expires_at = None
        if expires_in_days is not None:
            expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
                days=expires_in_days
            )

        secret_prefix = raw_secret[len(CLIENT_SECRET_PREFIX) :][:8]

        client = ServiceClient(
            name=name,
            client_id=client_id,
            client_secret_hash=secret_hash,
            secret_prefix=secret_prefix,
            scopes=scopes,
            user_id=user.id,
            expires_at=expires_at,
        )

        try:
            db.session.add(client)
            db.session.commit()
            logger.info(
                "Created OAuth2 client '%s' (client_id=%s) for user %s",
                name,
                client_id,
                user.email,
            )
        except Exception as exc:
            db.session.rollback()
            rollbar.report_exc_info()
            raise exc

        return raw_secret, client

    @staticmethod
    def list_clients(user):
        """Return all non-revoked service clients for *user*."""
        return (
            ServiceClient.query.filter_by(user_id=user.id, revoked=False)
            .order_by(ServiceClient.created_at.desc())
            .all()
        )

    @staticmethod
    def revoke_client(client_db_id, user):
        """Revoke a service client by its database UUID.

        Only the owner or an admin may revoke.
        """
        from gefapi.utils.permissions import is_admin_or_higher

        client = ServiceClient.query.get(client_db_id)
        if client is None:
            raise NotAllowed(message="Service client not found")
        if str(client.user_id) != str(user.id) and not is_admin_or_higher(user):
            raise NotAllowed(message="You can only revoke your own service clients")

        client.revoked = True
        try:
            db.session.commit()
            logger.info(
                "Revoked OAuth2 client '%s' (client_id=%s) for user %s",
                client.name,
                client.client_id,
                client.user_id,
            )
        except Exception as exc:
            db.session.rollback()
            rollbar.report_exc_info()
            raise exc
        return client

    # ------------------------------------------------------------------
    # Authentication (Client Credentials grant)
    # ------------------------------------------------------------------

    @staticmethod
    def authenticate(client_id, client_secret):
        """Validate client credentials and return the owning ``User``.

        Side-effect: updates ``last_used_at`` on the client.

        Raises
        ------
        AuthError
            If the client_id is unknown, the secret is wrong, or the
            client is expired / revoked.
        """
        client = ServiceClient.lookup(client_id)
        if client is None:
            raise AuthError(message="Unknown client_id")

        if not client.verify_secret(client_secret):
            raise AuthError(message="Invalid client_secret")

        if not client.is_valid():
            raise AuthError(message="Client is expired or revoked")

        # Touch last-used timestamp (best-effort)
        try:
            client.touch()
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.warning(
                "Failed to update last_used_at for client %s", client.client_id
            )

        return client.user
