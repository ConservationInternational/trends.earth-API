"""APP ACCESS SERVICE

Grants, requests, and revocations of per-application access for gated
first-party applications.
"""

from datetime import timedelta
import logging
import uuid

from sqlalchemy import func

from gefapi import db
from gefapi.models import OAuthClient, User
from gefapi.models.app_access import (
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REVOKED,
    AppAccessNotificationRecipient,
    UserAppAccess,
)
from gefapi.utils import mask_email, utcnow
from gefapi.utils.app_access import (
    APP_KEYS,
    default_role,
    get_app_policy,
    valid_app_key,
    valid_app_role,
)
from gefapi.utils.oidc_scopes import SCOPE_APP_ACCESS_WRITE
from gefapi.utils.security_events import log_security_event

logger = logging.getLogger(__name__)

# A user repeatedly bouncing off a gated application must not be able to spam
# the superadmins with notification email.
REQUEST_NOTIFICATION_COOLDOWN = timedelta(hours=24)
# Global roles that may be listed as access-request notification recipients.
NOTIFICATION_RECIPIENT_ROLES = ("ADMIN", "SUPERADMIN")


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

    # ------------------------------------------------------------------
    # Access-request notification recipients
    # ------------------------------------------------------------------

    @staticmethod
    def is_eligible_notification_recipient(user):
        """Only active global ADMIN/SUPERADMIN accounts may be listed."""
        return bool(
            user is not None
            and user.is_active
            and user.email
            and user.role in NOTIFICATION_RECIPIENT_ROLES
        )

    @staticmethod
    def listed_notification_recipients(app_key):
        """Return configured recipient rows for *app_key* (any eligibility)."""
        if not valid_app_key(app_key):
            raise AppAccessError(f"Unknown application '{app_key}'", status=404)
        return (
            AppAccessNotificationRecipient.query.filter_by(app_key=app_key)
            .join(User, AppAccessNotificationRecipient.user_id == User.id)
            .order_by(func.lower(User.email))
            .all()
        )

    @classmethod
    def set_notification_recipients(cls, app_key, user_ids, acting_user):
        """Replace *app_key*'s recipient list with *user_ids*.

        An empty list reverts to notifying all superadmins.  Raises
        :class:`AppAccessError` (400) listing ids that are unknown or not
        active ADMIN/SUPERADMIN accounts.
        """
        if not valid_app_key(app_key):
            raise AppAccessError(f"Unknown application '{app_key}'", status=404)
        if not isinstance(user_ids, list):
            raise AppAccessError("user_ids must be a list")

        parsed = {}
        invalid = []
        for raw in user_ids:
            try:
                parsed[uuid.UUID(str(raw))] = raw
            except (ValueError, TypeError, AttributeError):
                invalid.append(str(raw))
        users = (
            {user.id: user for user in User.query.filter(User.id.in_(parsed)).all()}
            if parsed
            else {}
        )
        for user_id, raw in parsed.items():
            if not cls.is_eligible_notification_recipient(users.get(user_id)):
                invalid.append(str(raw))
        if invalid:
            raise AppAccessError(
                "Recipients must be existing, active ADMIN or SUPERADMIN users",
                payload={"invalid_user_ids": sorted(set(invalid))},
            )

        existing = AppAccessNotificationRecipient.query.filter_by(app_key=app_key)
        before = sorted(str(row.user_id) for row in existing.all())
        existing.delete(synchronize_session=False)
        for user_id in parsed:
            db.session.add(
                AppAccessNotificationRecipient(
                    app_key=app_key,
                    user_id=user_id,
                    added_by_user_id=acting_user.id if acting_user else None,
                )
            )
        db.session.commit()

        after = sorted(str(user_id) for user_id in parsed)
        if before != after:
            log_security_event(
                "APP_ACCESS_NOTIFY_RECIPIENTS_CHANGED",
                user_id=str(acting_user.id) if acting_user else None,
                user_email=acting_user.email if acting_user else None,
                details={"app_key": app_key, "before": before, "after": after},
                level="info",
            )
        return cls.listed_notification_recipients(app_key)

    @staticmethod
    def app_approval_clients(app_key):
        """Active OAuth clients through which *app_key* admins can approve users."""
        return [
            client
            for client in OAuthClient.query.filter_by(
                required_app_key=app_key, is_active=True
            ).all()
            if SCOPE_APP_ACCESS_WRITE in (client.scopes or "").split()
        ]

    @classmethod
    def auto_app_admins(cls, app_key):
        """Users notified automatically because they can approve *app_key* users.

        Mirrors the authorization of
        ``PUT /api/v1/apps/<app_key>/users/<sub>/access``: an explicit active
        administering grant, an active verified account, and an application
        client registered for ``app-access:write``.  A superadmin's implicit
        app-admin status does not qualify on its own, so that an explicit
        recipient list really does replace the all-superadmin default.
        """
        policy = get_app_policy(app_key)
        if policy is None or not cls.app_approval_clients(app_key):
            return []
        return (
            User.query.join(UserAppAccess, UserAppAccess.user_id == User.id)
            .filter(
                UserAppAccess.app_key == app_key,
                UserAppAccess.status == STATUS_ACTIVE,
                UserAppAccess.role.in_(sorted(policy.admin_roles)),
                User.is_active.is_(True),
                User.email_verified.is_(True),
            )
            .order_by(func.lower(User.email))
            .all()
        )

    @classmethod
    def request_notification_plan(cls, app_key):
        """Return who to notify about *app_key* requests, and where to link.

        ``{"te_ui": [emails], "app_admin": [emails], "using_default": bool}``.
        Base recipients (eligible listed users, or all superadmins when there
        are none) review in the API UI.  Automatically included application
        admins who are not base recipients review in the application's own
        admin page.  Every address appears exactly once.
        """
        listed = [
            row.user.email
            for row in cls.listed_notification_recipients(app_key)
            if cls.is_eligible_notification_recipient(row.user)
        ]
        using_default = not listed
        base = _dedupe_emails(cls.superadmin_recipients() if using_default else listed)
        base_keys = {email.lower() for email in base}
        app_admins = _dedupe_emails(
            user.email
            for user in cls.auto_app_admins(app_key)
            if user.email and user.email.strip().lower() not in base_keys
        )
        return {"te_ui": base, "app_admin": app_admins, "using_default": using_default}

    @classmethod
    def request_notification_recipients(cls, app_key):
        """Every address notified about *app_key* access requests."""
        plan = cls.request_notification_plan(app_key)
        return _dedupe_emails(plan["te_ui"] + plan["app_admin"])

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
        recipients = _dedupe_emails(emails)
        logger.debug(
            "Resolved %s superadmin notification recipient(s): %s",
            len(recipients),
            ", ".join(mask_email(item) for item in recipients),
        )
        return recipients


def _dedupe_emails(emails):
    """Return sorted, case-insensitively de-duplicated, non-empty addresses."""
    seen = {}
    for email in emails:
        if email and email.strip():
            seen.setdefault(email.strip().lower(), email.strip())
    return sorted(seen.values(), key=str.lower)


def _roles_for(app_key):
    from gefapi.utils.app_access import app_roles_for

    return app_roles_for(app_key)


__all__ = ["APP_KEYS", "AppAccessError", "AppAccessService"]
