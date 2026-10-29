"""APP ACCESS NOTIFICATION TASKS

Email notifications for per-application access requests and decisions.  These
run on the Celery worker so that a mail outage can never fail an authorization
redirect or an API request.
"""

import html
import logging
import os
import urllib.parse

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


def _te_review_url(app_key):
    query = urllib.parse.urlencode(
        {"tab": "app-access", "app": app_key, "status": "pending"}
    )
    return f"{_ui_base_url()}/?{query}"


def _request_email_html(user, label, note, review_url):
    esc = html.escape
    body = (
        f"<p><strong>{esc(user.name or '')}</strong> ({esc(user.email or '')}) "
        f"has requested access to <strong>{esc(label)}</strong>.</p>"
        f"<p><strong>Institution:</strong> {esc(user.institution or 'n/a')}<br>"
        f"<strong>Note:</strong> {esc(note)}</p>"
    )
    if review_url:
        body += (
            f'<p><a href="{esc(review_url, quote=True)}">'
            "Review pending access requests</a></p>"
        )
    else:
        body += f"<p>Review the request in the {esc(label)} admin page.</p>"
    return body


@celery.task(base=AppAccessNotificationTask, bind=True)
def notify_superadmins_of_app_access_request(self, grant_id):
    """Notify reviewers that a user has requested access to an application.

    Configured (or default superadmin) recipients get a link to the API UI;
    application administrators who are not also in that group get a link to
    the application's own admin page.  Nobody receives more than one email.
    """
    from gefapi import app
    from gefapi.models.app_access import UserAppAccess
    from gefapi.services.app_access_service import AppAccessService
    from gefapi.services.email_service import EmailService
    from gefapi.utils.app_access import app_admin_url, app_label

    with app.app_context():
        grant = UserAppAccess.query.filter_by(id=grant_id).one_or_none()
        if grant is None:
            logger.warning("App access grant %s no longer exists", grant_id)
            return {"status": "skipped", "reason": "grant_not_found"}

        plan = AppAccessService.request_notification_plan(grant.app_key)
        groups = [
            (plan["te_ui"], _te_review_url(grant.app_key)),
            (plan["app_admin"], app_admin_url(grant.app_key)),
        ]
        if not any(recipients for recipients, _ in groups):
            logger.warning("No recipients configured for app access alerts")
            return {"status": "skipped", "reason": "no_recipients"}

        user = grant.user
        label = app_label(grant.app_key)
        note = grant.request_note or "(no note provided)"
        subject = f"[trends.earth] {label} access requested by {user.name}"
        sent = 0
        failures = []
        for recipients, review_url in groups:
            if not recipients:
                continue
            try:
                EmailService.send_html_email(
                    recipients=recipients,
                    html=_request_email_html(user, label, note, review_url),
                    subject=subject,
                    transactional=True,
                )
                sent += len(recipients)
            except Exception as exc:
                # Notification failures must never propagate to the requester.
                logger.error("Failed to send app access request notification: %s", exc)
                failures.append(str(exc))
        if failures and not sent:
            return {"status": "failed", "reason": "; ".join(failures)}
        result = {"status": "success", "recipients": sent}
        if failures:
            result["failures"] = failures
        return result


@celery.task(base=AppAccessNotificationTask, bind=True)
def notify_user_of_app_access_decision(self, grant_id, status):
    """Send an access decision regardless of optional notification preferences."""
    from gefapi import app
    from gefapi.models.app_access import STATUS_ACTIVE, UserAppAccess
    from gefapi.services.email_service import EmailService
    from gefapi.utils.app_access import app_label

    with app.app_context():
        grant = UserAppAccess.query.filter_by(id=grant_id).one_or_none()
        if grant is None:
            return {"status": "skipped", "reason": "grant_not_found"}
        user = grant.user
        if user is None:
            logger.warning("User for app access grant %s no longer exists", grant_id)
            return {"status": "skipped", "reason": "user_not_found"}

        label = app_label(grant.app_key)
        name = html.escape(user.name or "")
        safe_label = html.escape(label)
        if status == STATUS_ACTIVE:
            subject = f"[trends.earth] Your {label} access is active"
            body = (
                f"<p>Hi {name},</p>"
                f"<p>Your access to <strong>{safe_label}</strong> has been activated. "
                "You can now sign in.</p>"
            )
        else:
            subject = f"[trends.earth] Your {label} access has changed"
            body = (
                f"<p>Hi {name},</p>"
                f"<p>Your access to <strong>{safe_label}</strong> is not currently "
                "active. Please contact an administrator if you believe this is "
                "a mistake.</p>"
            )
        try:
            EmailService.send_html_email(
                recipients=[user.email],
                html=body,
                subject=subject,
                transactional=True,
            )
        except Exception as exc:
            logger.error("Failed to send app access decision notification: %s", exc)
            return {"status": "failed", "reason": str(exc)}
        return {"status": "success"}
