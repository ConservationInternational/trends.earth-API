"""BULK EMAIL SERVICE"""

import datetime
import logging

import nh3
import rollbar

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.errors import (
    BulkEmailAlreadySent,
    BulkEmailNotFound,
    NotApprovedSender,
    RecipientListNotFound,
    VerificationRequiredError,
)
from gefapi.models import User
from gefapi.models.bulk_email import BulkEmail
from gefapi.models.bulk_email_recipient_list import BulkEmailRecipientList
from gefapi.models.bulk_email_verification_token import (
    BulkEmailVerificationToken,
)
from gefapi.services.email_service import EmailService
from gefapi.utils.security_events import log_security_event

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10_000


# HTML tags and attributes allowed in bulk emails.
# This permits common email-safe formatting while blocking script injection.
_ALLOWED_EMAIL_TAGS = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "caption",
        "code",
        "del",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)


def _sanitize_html(html_content: str) -> str:
    """Strip dangerous tags/attributes from bulk email HTML before storage."""
    if html_content is None:
        return html_content
    return nh3.clean(html_content, tags=_ALLOWED_EMAIL_TAGS)


def _utcnow():
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _approved_senders():
    """Return the set of approved sender emails (lowercase)."""
    return set(SETTINGS.get("BULK_EMAIL_APPROVED_SENDERS", []))


def _max_recipients():
    return SETTINGS.get("BULK_EMAIL_MAX_RECIPIENTS", 50)


def _from_email():
    return SETTINGS.get("BULK_EMAIL_FROM_EMAIL", "noreply@trends.earth")


def _check_approved_sender(user):
    """Raise NotApprovedSender if user is not on the approved senders list."""
    approved = _approved_senders()
    if approved and user.email.lower() not in approved:
        raise NotApprovedSender(
            f"User {user.email!r} is not an approved bulk email sender."
        )


# ---------------------------------------------------------------------------
# Recipient list helpers
# ---------------------------------------------------------------------------


def _build_recipient_query(filter_criteria):
    """Build a SQLAlchemy query for User based on filter_criteria dict.

    Supported keys:
      roles: list[str] â€” e.g. ["USER", "ADMIN"]
      min_created_at: ISO datetime string
      max_created_at: ISO datetime string
      min_last_activity_at: ISO datetime string
      max_last_activity_at: ISO datetime string
      email_verified: bool | None
    """
    q = db.session.query(User)

    roles = filter_criteria.get("roles")
    if roles:
        q = q.filter(User.role.in_(roles))

    min_created = filter_criteria.get("min_created_at")
    if min_created:
        q = q.filter(User.created_at >= datetime.datetime.fromisoformat(min_created))

    max_created = filter_criteria.get("max_created_at")
    if max_created:
        q = q.filter(User.created_at <= datetime.datetime.fromisoformat(max_created))

    min_activity = filter_criteria.get("min_last_activity_at")
    if min_activity:
        q = q.filter(
            User.last_activity_at >= datetime.datetime.fromisoformat(min_activity)
        )

    max_activity = filter_criteria.get("max_last_activity_at")
    if max_activity:
        q = q.filter(
            User.last_activity_at <= datetime.datetime.fromisoformat(max_activity)
        )

    email_verified = filter_criteria.get("email_verified")
    if email_verified is not None:
        q = q.filter(User.email_verified == email_verified)

    return q


# ---------------------------------------------------------------------------
# Recipient list CRUD
# ---------------------------------------------------------------------------


class BulkEmailService:
    """Service layer for Bulk Email."""

    # -- Recipient Lists --

    @staticmethod
    def list_recipient_lists():
        return (
            db.session.query(BulkEmailRecipientList)
            .order_by(BulkEmailRecipientList.created_at.desc())
            .all()
        )

    @staticmethod
    def create_recipient_list(name, description, filter_criteria, created_by_id):
        count = _build_recipient_query(filter_criteria).count()
        rl = BulkEmailRecipientList(
            name=name,
            description=description,
            filter_criteria=filter_criteria,
            estimated_count=count,
            created_by_id=created_by_id,
        )
        db.session.add(rl)
        db.session.commit()
        log_security_event(
            "BULK_EMAIL_RECIPIENT_LIST_CREATED",
            user_id=str(created_by_id),
            details={"list_id": str(rl.id), "name": name, "estimated_count": count},
        )
        return rl

    @staticmethod
    def delete_recipient_list(list_id):
        rl = db.session.get(BulkEmailRecipientList, str(list_id))
        if not rl:
            raise RecipientListNotFound(f"Recipient list {list_id!r} not found.")
        db.session.delete(rl)
        db.session.commit()

    @staticmethod
    def preview_recipients(filter_criteria, limit=20):
        """Return total count and a sample of recipients matching filter_criteria."""
        q = _build_recipient_query(filter_criteria)
        total = q.count()
        sample = q.limit(limit).all()
        return {
            "total": total,
            "sample": [
                {"id": str(u.id), "email": u.email, "name": u.name, "role": u.role}
                for u in sample
            ],
        }

    # -- Bulk Emails --

    @staticmethod
    def list_bulk_emails(status=None):
        q = db.session.query(BulkEmail).order_by(BulkEmail.created_at.desc())
        if status:
            q = q.filter(BulkEmail.status == status.upper())
        return q.all()

    @staticmethod
    def get_bulk_email(bulk_email_id):
        c = db.session.get(BulkEmail, str(bulk_email_id))
        if not c:
            raise BulkEmailNotFound(f"Bulk email {bulk_email_id!r} not found.")
        return c

    @staticmethod
    def create_bulk_email(
        name, subject, html_content, created_by_id, recipient_list_id=None
    ):
        c = BulkEmail(
            name=name,
            subject=subject,
            html_content=_sanitize_html(html_content),
            status="DRAFT",
            recipient_list_id=str(recipient_list_id) if recipient_list_id else None,
            created_by_id=str(created_by_id),
        )
        db.session.add(c)
        db.session.commit()
        log_security_event(
            "BULK_EMAIL_DRAFT_CREATED",
            user_id=str(created_by_id),
            details={"bulk_email_id": str(c.id), "name": name},
        )
        return c

    @staticmethod
    def update_bulk_email(bulk_email_id, user_id, **kwargs):
        c = db.session.get(BulkEmail, str(bulk_email_id))
        if not c:
            raise BulkEmailNotFound(f"Bulk email {bulk_email_id!r} not found.")
        if c.status != "DRAFT":
            raise BulkEmailAlreadySent(
                "Cannot update a bulk email that has already been sent."
            )
        for field in ("name", "subject", "html_content", "recipient_list_id"):
            if field in kwargs and kwargs[field] is not None:
                value = kwargs[field]
                if field == "html_content":
                    value = _sanitize_html(value)
                setattr(c, field, value)
        c.updated_at = _utcnow()
        db.session.commit()
        log_security_event(
            "BULK_EMAIL_DRAFT_UPDATED",
            user_id=str(user_id),
            details={"bulk_email_id": str(bulk_email_id)},
        )
        return c

    @staticmethod
    def delete_bulk_email(bulk_email_id):
        c = db.session.get(BulkEmail, str(bulk_email_id))
        if not c:
            raise BulkEmailNotFound(f"Bulk email {bulk_email_id!r} not found.")
        if c.status != "DRAFT":
            raise BulkEmailAlreadySent(
                "Cannot delete a bulk email that has already been sent."
            )
        db.session.delete(c)
        db.session.commit()

    # -- Verification OTP --

    @staticmethod
    def generate_verification_code(user_id, bulk_email_id):
        """Invalidate prior unused OTPs, create a new one, and email it."""
        # Load user first so we can gate on approved-sender *before* any DB writes
        user = db.session.get(User, str(user_id))
        if not user:
            raise BulkEmailNotFound(f"User {user_id!r} not found.")
        _check_approved_sender(user)  # raises NotApprovedSender if not listed

        # Verify bulk email exists
        c = db.session.get(BulkEmail, str(bulk_email_id))
        if not c:
            raise BulkEmailNotFound(f"Bulk email {bulk_email_id!r} not found.")

        # Invalidate prior unused tokens for this user+bulk_email
        prior = (
            db.session.query(BulkEmailVerificationToken)
            .filter(
                BulkEmailVerificationToken.user_id == str(user_id),
                BulkEmailVerificationToken.bulk_email_id == str(bulk_email_id),
                BulkEmailVerificationToken.used_at.is_(None),
            )
            .all()
        )
        now = _utcnow()
        for t in prior:
            t.used_at = now  # mark superseded

        otp = BulkEmailVerificationToken(
            user_id=str(user_id), bulk_email_id=str(bulk_email_id)
        )
        db.session.add(otp)
        db.session.commit()

        # Send OTP via email
        html_body = (
            f"<p>Your Trends.Earth Bulk Email verification code is:</p>"
            f"<h2 style='letter-spacing:0.2em'>{otp.token}</h2>"
            f"<p>This code expires in 15 minutes and is valid only for bulk email "
            f"<strong>{c.name}</strong>.</p>"
            f"<p>If you did not request this code, please ignore this email.</p>"
        )
        EmailService.send_html_email(
            recipients=[{"address": user.email, "name": user.name}],
            html=html_body,
            from_email=_from_email(),
            subject="[Trends.Earth] Bulk Email Send Verification Code",
        )

        log_security_event(
            "BULK_EMAIL_VERIFICATION_CODE_SENT",
            user_id=str(user_id),
            details={"bulk_email_id": str(bulk_email_id)},
        )
        return otp

    # -- Resolve recipient count (cheap, no full fetch) --

    @staticmethod
    def resolve_recipient_count(bulk_email_id):
        c = db.session.get(BulkEmail, str(bulk_email_id))
        if not c:
            raise BulkEmailNotFound(f"Bulk email {bulk_email_id!r} not found.")
        if not c.recipient_list_id:
            return 0
        rl = db.session.get(BulkEmailRecipientList, str(c.recipient_list_id))
        if not rl:
            return 0
        return _build_recipient_query(rl.filter_criteria).count()

    # -- Send --

    @staticmethod
    def send_bulk_email(bulk_email_id, user, code=None):
        """Send a bulk email.

        If recipient count > BULK_EMAIL_MAX_RECIPIENTS and code is None,
        raises VerificationRequiredError (HTTP 428).
        If code is supplied, it is verified before sending.
        """
        _check_approved_sender(user)

        c = db.session.get(BulkEmail, str(bulk_email_id))
        if not c:
            raise BulkEmailNotFound(f"Bulk email {bulk_email_id!r} not found.")
        if c.status == "SENT":
            raise BulkEmailAlreadySent("Bulk email has already been sent.")

        recipient_count = BulkEmailService.resolve_recipient_count(bulk_email_id)
        max_r = _max_recipients()

        if recipient_count > max_r:
            if code is None:
                log_security_event(
                    "BULK_EMAIL_SEND_ATTEMPTED",
                    user_id=str(user.id),
                    details={
                        "bulk_email_id": str(bulk_email_id),
                        "recipient_count": recipient_count,
                        "verification_required": True,
                    },
                )
                raise VerificationRequiredError(
                    (
                        f"Verification required: {recipient_count} recipients"
                        f" exceeds threshold {max_r}."
                    ),
                    recipient_count=recipient_count,
                )

            # Verify OTP — burn-on-wrong-guess to prevent brute force.
            # First: find the most recent unused OTP for this user+bulk_email
            # WITHOUT filtering on the code itself, so we can mark it used
            # even when the code is wrong.
            otp = (
                db.session.query(BulkEmailVerificationToken)
                .filter(
                    BulkEmailVerificationToken.user_id == str(user.id),
                    BulkEmailVerificationToken.bulk_email_id == str(bulk_email_id),
                    BulkEmailVerificationToken.used_at.is_(None),
                )
                .order_by(BulkEmailVerificationToken.created_at.desc())
                .first()
            )
            if not otp or otp.is_expired:
                from gefapi.errors import AuthError

                raise AuthError("Invalid or expired verification code.")
            if otp.token != str(code):
                # Burn the OTP on wrong guess — caller must request a new one
                otp.used_at = _utcnow()
                db.session.flush()
                from gefapi.errors import AuthError

                log_security_event(
                    "BULK_EMAIL_VERIFICATION_WRONG_CODE",
                    user_id=str(user.id),
                    details={"bulk_email_id": str(bulk_email_id)},
                )
                raise AuthError(
                    "Invalid verification code. Request a new code and try again."
                )
            otp.used_at = _utcnow()
            db.session.flush()

        # Resolve full recipient list
        rl = (
            db.session.get(BulkEmailRecipientList, str(c.recipient_list_id))
            if c.recipient_list_id
            else None
        )
        recipients = []
        if rl:
            users = _build_recipient_query(rl.filter_criteria).all()
            recipients = [
                {
                    "address": {"email": u.email, "name": u.name},
                    "substitution_data": {"name": u.name, "email": u.email},
                }
                for u in users
            ]

        try:
            for i in range(0, max(1, len(recipients)), _BATCH_SIZE):
                batch = recipients[i : i + _BATCH_SIZE]
                if not batch:
                    break
                EmailService.send_html_email(
                    recipients=batch,
                    html=c.html_content,
                    from_email=_from_email(),
                    subject=c.subject,
                )
        except Exception:
            log_security_event(
                "BULK_EMAIL_SEND_FAILED",
                user_id=str(user.id),
                details={"bulk_email_id": str(bulk_email_id)},
            )
            rollbar.report_exc_info()
            raise

        c.status = "SENT"
        c.sent_by_id = str(user.id)
        c.sent_at = _utcnow()
        c.recipient_count = len(recipients)
        db.session.commit()

        log_security_event(
            "BULK_EMAIL_SEND_SUCCESS",
            user_id=str(user.id),
            details={
                "bulk_email_id": str(bulk_email_id),
                "recipient_count": len(recipients),
            },
        )
        return c

    @staticmethod
    def send_test_bulk_email(bulk_email_id, user):
        """Send bulk email as test to all superadmins (no 2FA, no status change)."""
        _check_approved_sender(user)

        c = db.session.get(BulkEmail, str(bulk_email_id))
        if not c:
            raise BulkEmailNotFound(f"Bulk email {bulk_email_id!r} not found.")

        superadmins = db.session.query(User).filter(User.role == "SUPERADMIN").all()
        recipients = [
            {
                "address": {"email": u.email, "name": u.name},
                "substitution_data": {"name": u.name, "email": u.email},
            }
            for u in superadmins
        ]

        test_subject = f"[TEST] {c.subject}"
        EmailService.send_html_email(
            recipients=recipients,
            html=c.html_content,
            from_email=_from_email(),
            subject=test_subject,
        )

        log_security_event(
            "BULK_EMAIL_TEST_SEND",
            user_id=str(user.id),
            details={
                "bulk_email_id": str(bulk_email_id),
                "superadmin_count": len(recipients),
            },
        )
        return {"superadmin_count": len(recipients)}

    @staticmethod
    def get_config():
        """Return bulk email configuration for display in the UI."""
        return {
            "max_recipients": _max_recipients(),
            "from_email": _from_email(),
        }
