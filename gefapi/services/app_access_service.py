"""APP ACCESS SERVICE

Grants, requests, and revocations of per-application access for gated
first-party applications.
"""

from datetime import timedelta
import logging

from gefapi import db
from gefapi.models import OAuthClient, User
from gefapi.models.app_access import (
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REVOKED,
    UserAppAccess,
)
from gefapi.utils import mask_email, utcnow
from gefapi.utils.app_access import (
    APP_KEYS,
    default_role,
    valid_app_key,
    valid_app_role,
)
from gefapi.utils.security_events import log_security_event

logger = logging.getLogger(__name__)

# A user repeatedly bouncing off a gated application must not be able to spam
# the superadmins with notification email.
REQUEST_NOTIFICATION_COOLDOWN = timedelta(hours=24)


class AppAccessError(Exception):
    """Raised when an app-access operation is invalid."""

    def __init__(self, message, status=400, payload=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.payload = payload or {}


class AppAccessService:
    """Business logic for ``user_app_access`` rows."""

    @staticmethod
    def get_grant(user_id, app_key):
        if not valid_app_key(app_key):
            raise AppAccessError(f"Unknown application '{app_key}'")
        return UserAppAccess.query.filter_by(
            user_id=user_id, app_key=app_key
        ).one_or_none()

    @staticmethod
    def list_grants(app_key=None, status=None, role=None, user_id=None):
        query = UserAppAccess.query
        if app_key:
            if not valid_app_key(app_key):
                raise AppAccessError(f"Unknown application '{app_key}'")
            query = query.filter_by(app_key=app_key)
        if status:
            query = query.filter_by(status=status)
        if role:
            query = query.filter_by(role=role)
        if user_id:
            query = query.filter_by(user_id=user_id)
        return query.order_by(UserAppAccess.created_at.desc(), UserAppAccess.app_key)

    @staticmethod
    def list_for_user(user_id):
        return (
            UserAppAccess.query.filter_by(user_id=user_id)
            .order_by(UserAppAccess.app_key)
            .all()
        )

    @classmethod
    def request_access(cls, user, app_key, request_note=None, notify=True):
        """Create or update a pending access request for *user*.

        Idempotent.  Returns ``(grant, created)`` where *created* indicates
        whether a brand-new request was recorded.  Raises
        :class:`AppAccessError` with status 403 when access was explicitly
        revoked by an administrator — reopening is an admin action.
        """
        if not valid_app_key(app_key):
            raise AppAccessError(f"Unknown application '{app_key}'", status=404)

        grant = cls.get_grant(user.id, app_key)
        if grant is not None and grant.status == STATUS_ACTIVE:
            return grant, False
        if grant is not None and grant.status == STATUS_REVOKED:
            raise AppAccessError(
                "Access to this application has been removed by an administrator",
                status=403,
                payload={"app_key": app_key, "status": STATUS_REVOKED},
            )

        created = grant is None
        if created:
            grant = UserAppAccess(
                user_id=user.id,
                app_key=app_key,
                status=STATUS_PENDING,
                role=default_role(app_key),
                requested_at=utcnow(),
                request_note=request_note,
            )
            db.session.add(grant)
        elif request_note:
            grant.request_note = request_note

        should_notify = notify and cls._notification_due(grant, created)
        if should_notify:
            grant.notified_at = utcnow()
        db.session.commit()

        if created:
            log_security_event(
                "APP_ACCESS_REQUESTED",
                user_id=str(user.id),
                user_email=user.email,
                details={"app_key": app_key},
                level="info",
            )
        if should_notify:
            cls._dispatch_request_notification(grant.id)
        return grant, created

    @staticmethod
    def _notification_due(grant, created):
        if created:
            return True
        if grant.notified_at is None:
            return True
        return utcnow() - grant.notified_at >= REQUEST_NOTIFICATION_COOLDOWN

    @staticmethod
    def _dispatch_request_notification(grant_id):
        """Queue the superadmin notification; never fail the caller."""
        try:
            from gefapi.tasks.app_access_notifications import (
                notify_superadmins_of_app_access_request,
            )

            notify_superadmins_of_app_access_request.delay(str(grant_id))
        except Exception as exc:  # noqa: BLE001  # notification must never fail the caller
            logger.warning(
                "Failed to queue app access request notification for %s: %s",
                grant_id,
                exc,
            )

    @classmethod
    def set_access(
        cls,
        user_id,
        app_key,
        status,
        role=None,
        note=None,
        acting_user=None,
    ):
        """Grant, update, or revoke *user_id*'s access to *app_key*."""
        if not valid_app_key(app_key):
            raise AppAccessError(f"Unknown application '{app_key}'", status=400)
        if status not in (STATUS_PENDING, STATUS_ACTIVE, STATUS_REVOKED):
            raise AppAccessError(f"Invalid status '{status}'")
        if role is not None and not valid_app_role(app_key, role):
            raise AppAccessError(
                f"Invalid role '{role}' for application '{app_key}'. "
                f"Valid roles: {', '.join(sorted(_roles_for(app_key)))}"
            )

        user = db.session.get(User, user_id)
        if user is None:
            raise AppAccessError("User not found", status=404)

        grant = cls.get_grant(user_id, app_key)
        previous_status = grant.status if grant else None
        if grant is None:
            grant = UserAppAccess(
                user_id=user_id,
                app_key=app_key,
                status=status,
                role=role or default_role(app_key),
                note=note,
            )
            db.session.add(grant)
        else:
            grant.status = status
            if role is not None:
                grant.role = role
            if note is not None:
                grant.note = note

        if status == STATUS_ACTIVE:
            # Preserve granted_at when only the role is being changed.
            if previous_status != STATUS_ACTIVE:
                grant.granted_at = utcnow()
            grant.revoked_at = None
            if acting_user is not None:
                grant.granted_by_user_id = acting_user.id
        elif status == STATUS_REVOKED:
            grant.revoked_at = utcnow()
            if acting_user is not None:
                grant.granted_by_user_id = acting_user.id

        db.session.commit()

        if status == STATUS_REVOKED and previous_status != STATUS_REVOKED:
            cls._revoke_client_sessions(user_id, app_key)

        if previous_status != status:
            log_security_event(
                "APP_ACCESS_GRANTED"
                if status == STATUS_ACTIVE
                else "APP_ACCESS_REVOKED",
                user_id=str(user_id),
                user_email=user.email,
                details={
                    "app_key": app_key,
                    "status": status,
                    "role": grant.role,
                    "previous_status": previous_status,
                    "acting_user_id": str(acting_user.id) if acting_user else None,
                },
                level="info",
            )
            cls._dispatch_decision_notification(grant.id, status)
        return grant

    @staticmethod
    def _revoke_client_sessions(user_id, app_key):
        """Revoke OIDC refresh tokens for clients gated on *app_key*.

        Deliberately scoped to ``OIDCRefreshToken`` — the ``RefreshToken``
        model backs legacy ``POST /auth`` sessions used by the QGIS plugin and
        the API UI and must never be touched here.
        """
        from gefapi.services.oidc_service import (
            revoke_oidc_refresh_tokens_for_user_and_client,
        )

        client_ids = [
            client.client_id
            for client in OAuthClient.query.filter_by(required_app_key=app_key).all()
        ]
        revoked = 0
        for client_id in client_ids:
            revoked += revoke_oidc_refresh_tokens_for_user_and_client(
                user_id, client_id
            )
        if revoked:
            logger.info(
                "Revoked %s OIDC refresh token(s) for user %s after losing "
                "access to %s",
                revoked,
                user_id,
                app_key,
            )
        return revoked

    @staticmethod
    def _dispatch_decision_notification(grant_id, status):
        try:
            from gefapi.tasks.app_access_notifications import (
                notify_user_of_app_access_decision,
            )

            notify_user_of_app_access_decision.delay(str(grant_id), status)
        except Exception as exc:  # noqa: BLE001  # notification must never fail the caller
            logger.warning(
                "Failed to queue app access decision notification for %s: %s",
                grant_id,
                exc,
            )

    @staticmethod
    def superadmin_recipients():
        """Return the de-duplicated superadmin notification address list."""
        from gefapi.utils.permissions import _configured_admin_email

        emails = [
            user.email
            for user in User.query.filter_by(role="SUPERADMIN", is_active=True).all()
            if user.email
        ]
        configured = _configured_admin_email()
        if configured:
            emails.append(configured)
        seen = {}
        for email in emails:
            seen.setdefault(email.strip().lower(), email.strip())
        recipients = sorted(seen.values())
        logger.debug(
            "Resolved %s superadmin notification recipient(s): %s",
            len(recipients),
            ", ".join(mask_email(item) for item in recipients),
        )
        return recipients


def _roles_for(app_key):
    from gefapi.utils.app_access import APP_ROLES

    return APP_ROLES.get(app_key, ())


__all__ = ["APP_KEYS", "AppAccessError", "AppAccessService"]
