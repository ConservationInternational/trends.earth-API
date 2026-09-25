"""APP ACCESS NOTIFICATION TASKS

Email notifications for per-application access requests and decisions.  These
run on the Celery worker so that a mail outage can never fail an authorization
redirect or an API request.
"""

import logging
import os

from celery import Task
import rollbar

logger = logging.getLogger(__name__)


class AppAccessNotificationTask(Task):
    """Base task for app access notifications."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"App access notification task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


def _ui_base_url():
    return (os.getenv("API_UI_URL") or "https://api.trends.earth").rstrip("/")


@celery.task(base=AppAccessNotificationTask, bind=True)
def notify_superadmins_of_app_access_request(self, grant_id):
    """Notify superadmins that a user has requested access to an application."""
    from gefapi import app
    from gefapi.models.app_access import UserAppAccess
    from gefapi.services.app_access_service import AppAccessService
    from gefapi.services.email_service import EmailService
    from gefapi.utils.app_access import app_label

    with app.app_context():
        grant = UserAppAccess.query.filter_by(id=grant_id).one_or_none()
        if grant is None:
            logger.warning("App access grant %s no longer exists", grant_id)
            return {"status": "skipped", "reason": "grant_not_found"}

        recipients = AppAccessService.superadmin_recipients()
        if not recipients:
            logger.warning("No superadmin recipients configured for app access alerts")
            return {"status": "skipped", "reason": "no_recipients"}

        user = grant.user
        label = app_label(grant.app_key)
        queue_url = f"{_ui_base_url()}/?tab=app-access&status=pending"
        note = grant.request_note or "(no note provided)"
        html = (
            f"<p><strong>{user.name}</strong> ({user.email}) has requested "
            f"access to <strong>{label}</strong>.</p>"
            f"<p><strong>Institution:</strong> {user.institution or 'n/a'}<br>"
            f"<strong>Note:</strong> {note}</p>"
            f'<p><a href="{queue_url}">Review pending access requests</a></p>'
        )
        try:
            EmailService.send_html_email(
                recipients=recipients,
                html=html,
                subject=f"[trends.earth] {label} access requested by {user.name}",
                transactional=True,
            )
        except Exception as exc:
            # Notification failures must never propagate to the requester.
            logger.error("Failed to send app access request notification: %s", exc)
            return {"status": "failed", "reason": str(exc)}
        return {"status": "success", "recipients": len(recipients)}


@celery.task(base=AppAccessNotificationTask, bind=True)
def notify_user_of_app_access_decision(self, grant_id, status):
    """Notify a user that their application access was granted or revoked."""
    from gefapi import app
    from gefapi.models.app_access import STATUS_ACTIVE, UserAppAccess
    from gefapi.services.email_service import EmailService
    from gefapi.utils.app_access import app_label

    with app.app_context():
        grant = UserAppAccess.query.filter_by(id=grant_id).one_or_none()
        if grant is None:
            return {"status": "skipped", "reason": "grant_not_found"}
        user = grant.user
        if user is None or not user.email_notifications_enabled:
            return {"status": "skipped", "reason": "notifications_disabled"}

        label = app_label(grant.app_key)
        if status == STATUS_ACTIVE:
            subject = f"[trends.earth] Your {label} access is active"
            html = (
                f"<p>Hi {user.name},</p>"
                f"<p>Your access to <strong>{label}</strong> has been activated. "
                "You can now sign in.</p>"
            )
        else:
            subject = f"[trends.earth] Your {label} access has changed"
            html = (
                f"<p>Hi {user.name},</p>"
                f"<p>Your access to <strong>{label}</strong> is not currently "
                "active. Please contact an administrator if you believe this is "
                "a mistake.</p>"
            )
        try:
            EmailService.send_html_email(
                recipients=[user.email],
                html=html,
                subject=subject,
                transactional=True,
            )
        except Exception as exc:
            logger.error("Failed to send app access decision notification: %s", exc)
            return {"status": "failed", "reason": str(exc)}
        return {"status": "success"}
