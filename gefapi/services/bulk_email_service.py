"""BULK EMAIL SERVICE"""

import datetime
import logging

import jwt as pyjwt
from markupsafe import escape as html_escape
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

# Per-tag attribute allowlist.  The ``style`` attribute is explicitly
# permitted so that inline CSS used for email layout (table widths, padding,
# colours, font sizes) is preserved.  CSS injection risk is low in this
# context: only approved superadmins can author bulk emails, and email
# clients already sandbox CSS to the message scope.
_ALLOWED_EMAIL_ATTRIBUTES = {
    "a": {"href", "rel", "style", "target"},
    "b": {"style"},
    "blockquote": {"cite", "style"},
    "br": set(),
    "caption": {"style"},
    "code": {"style"},
    "del": {"style"},
    "em": {"style"},
    "h1": {"style"},
    "h2": {"style"},
    "h3": {"style"},
    "h4": {"style"},
    "h5": {"style"},
    "h6": {"style"},
    "hr": {"style"},
    "i": {"style"},
    "img": {"alt", "height", "src", "style", "width"},
    "li": {"style"},
    "ol": {"start", "style", "type"},
    "p": {"style"},
    "pre": {"style"},
    "s": {"style"},
    "span": {"style"},
    "strong": {"style"},
    "table": {"align", "border", "cellpadding", "cellspacing", "style", "width"},
    "tbody": {"style"},
    "td": {"align", "colspan", "rowspan", "style", "valign", "width"},
    "th": {"align", "colspan", "rowspan", "scope", "style", "valign", "width"},
    "thead": {"style"},
    "tr": {"style"},
    "u": {"style"},
    "ul": {"style"},
}


def _sanitize_html(html_content: str) -> str:
    """Strip dangerous tags/attributes from bulk email HTML before storage."""
    if html_content is None:
        return html_content
    return nh3.clean(
        html_content,
        tags=_ALLOWED_EMAIL_TAGS,
        attributes=_ALLOWED_EMAIL_ATTRIBUTES,
        link_rel=None,
    )


def _utcnow():
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _approved_senders():
    """Return the set of approved sender emails (lowercase)."""
    return set(SETTINGS.get("BULK_EMAIL_APPROVED_SENDERS", []))


def _max_recipients():
    return SETTINGS.get("BULK_EMAIL_MAX_RECIPIENTS", 50)


def _from_email():
    return SETTINGS.get("BULK_EMAIL_FROM_EMAIL", "noreply@trends.earth")


def _api_ui_url():
    # Strip trailing slash to prevent double-slash in constructed URLs.
    return SETTINGS.get("API_UI_URL", "https://api.trends.earth").rstrip("/")


def _unsubscribe_secret():
    """Return the JWT secret for unsubscribe tokens.

    Uses the dedicated UNSUBSCRIBE_JWT_SECRET when set, falling back to
    JWT_SECRET_KEY.  Keeping these separate means rotating the auth secret
    does not invalidate outstanding unsubscribe links.
    """
    return SETTINGS.get("UNSUBSCRIBE_JWT_SECRET") or SETTINGS.get("JWT_SECRET_KEY")


def _generate_unsubscribe_token(user_id):
    """Generate a signed JWT unsubscribe token for a user."""
    secret = _unsubscribe_secret()
    expiry_days = SETTINGS.get("UNSUBSCRIBE_TOKEN_EXPIRY_DAYS", 30)
    # Use timezone-aware datetime so the exp claim is an unambiguous Unix
    # timestamp — avoids a PyJWT implementation-detail dependency on naive
    # datetimes being treated as UTC.
    exp = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=expiry_days)
    return pyjwt.encode(
        {"sub": str(user_id), "purpose": "unsubscribe", "exp": int(exp.timestamp())},
        secret,
        algorithm="HS256",
    )


def _unsubscribe_footer_html(user):
    """Return an HTML unsubscribe/manage-preferences snippet for a user."""
    token = _generate_unsubscribe_token(user.id)
    url = f"{_api_ui_url()}/unsubscribe?token={token}"
    return (
        '<p style="font-size:12px;color:#6c757d;text-align:center;margin:0;">'
        f'<a href="{url}" style="color:#c8272a;text-decoration:underline;">'
        "Unsubscribe or manage email preferences"
        "</a>"
        "</p>"
    )


def _check_approved_sender(user):
    """Raise NotApprovedSender if user is not on the approved senders list.

    Deliberately fails-safe: if the approved senders list is empty (e.g. the
    environment variable was not set), the check raises rather than allowing
    every superadmin to send bulk email.
    """
    approved = _approved_senders()
    if not approved or user.email.lower() not in approved:
        raise NotApprovedSender(
            f"User {user.email!r} is not an approved bulk email sender."
        )


# ---------------------------------------------------------------------------
# Recipient list helpers
# ---------------------------------------------------------------------------

_PREVIEW_ALLOWED_SORT_FIELDS = {
    "email",
    "name",
    "role",
    "email_verified",
    "created_at",
    "last_activity_at",
}

_KNOWN_ROLES = {"USER", "ADMIN", "SUPERADMIN"}


def _parse_filter_datetime(value, field_name):
    """Parse an ISO 8601 datetime string, raising ValueError with a friendly message."""
    try:
        return datetime.datetime.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"Invalid datetime for '{field_name}': {value!r}. "
            "Expected ISO 8601 format, e.g. '2024-01-01T00:00:00'."
        ) from exc


def _build_recipient_query(filter_criteria):
    """Build a SQLAlchemy query for User based on filter_criteria dict.

    Supported keys:
      roles: list[str] — e.g. ["USER", "ADMIN"]
      min_created_at: ISO datetime string
      max_created_at: ISO datetime string
      min_last_activity_at: ISO datetime string
      max_last_activity_at: ISO datetime string
      email_verified: bool | None

    Raises ValueError for unrecognised role names or malformed datetime strings
    so that callers can return HTTP 400 rather than a leaky 500.
    """
    q = db.session.query(User)

    roles = filter_criteria.get("roles")
    if roles:
        unknown = [r for r in roles if r not in _KNOWN_ROLES]
        if unknown:
            raise ValueError(
                f"Unknown role(s): {unknown!r}. Valid values: {sorted(_KNOWN_ROLES)}."
            )
        q = q.filter(User.role.in_(roles))

    min_created = filter_criteria.get("min_created_at")
    if min_created:
        q = q.filter(
            User.created_at >= _parse_filter_datetime(min_created, "min_created_at")
        )

    max_created = filter_criteria.get("max_created_at")
    if max_created:
        q = q.filter(
            User.created_at <= _parse_filter_datetime(max_created, "max_created_at")
        )

    min_activity = filter_criteria.get("min_last_activity_at")
    if min_activity:
        q = q.filter(
            User.last_activity_at
            >= _parse_filter_datetime(min_activity, "min_last_activity_at")
        )

    max_activity = filter_criteria.get("max_last_activity_at")
    if max_activity:
        q = q.filter(
            User.last_activity_at
            <= _parse_filter_datetime(max_activity, "max_last_activity_at")
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
    def update_recipient_list(
        list_id, name=None, description=None, filter_criteria=None
    ):
        rl = db.session.get(BulkEmailRecipientList, str(list_id))
        if not rl:
            raise RecipientListNotFound(f"Recipient list {list_id!r} not found.")
        if name is not None:
            rl.name = name
        if description is not None:
            rl.description = description
        if filter_criteria is not None:
            rl.filter_criteria = filter_criteria
            rl.estimated_count = _build_recipient_query(filter_criteria).count()
        db.session.commit()
        return rl

    @staticmethod
    def delete_recipient_list(list_id):
        rl = db.session.get(BulkEmailRecipientList, str(list_id))
        if not rl:
            raise RecipientListNotFound(f"Recipient list {list_id!r} not found.")
        db.session.delete(rl)
        db.session.commit()

    @staticmethod
    def preview_recipients(filter_criteria, page=1, per_page=100, sort=None):
        """Return total count and a page of recipients matching filter_criteria."""
        q = _build_recipient_query(filter_criteria)
        total = q.count()
        if sort:
            from gefapi.utils.query_filters import parse_sort_param

            order_clauses = parse_sort_param(
                sort,
                allowed_fields=_PREVIEW_ALLOWED_SORT_FIELDS,
                resolve_column=lambda field, _dir: getattr(User, field, None),
            )
            q = q.order_by(*order_clauses) if order_clauses else q.order_by(User.email)
        else:
            q = q.order_by(User.email)
        offset = (max(page, 1) - 1) * per_page
        sample = q.offset(offset).limit(per_page).all()
        return {
            "total": total,
            "sample": [
                {
                    "id": str(u.id),
                    "email": u.email,
                    "name": u.name,
                    "role": u.role,
                    "email_verified": u.email_verified,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                    "last_activity_at": u.last_activity_at.isoformat()
                    if u.last_activity_at
                    else None,
                }
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
        name,
        subject,
        html_content,
        created_by_id,
        recipient_list_id=None,
        subscription_type=None,
        fields_data=None,
    ):
        c = BulkEmail(
            name=name,
            subject=subject,
            html_content=_sanitize_html(html_content),
            status="DRAFT",
            recipient_list_id=str(recipient_list_id) if recipient_list_id else None,
            created_by_id=str(created_by_id),
            subscription_type=subscription_type or None,
            fields_data=fields_data if isinstance(fields_data, dict) else None,
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
        # subscription_type is nullable — allow explicit None to clear it
        if "subscription_type" in kwargs:
            c.subscription_type = kwargs["subscription_type"] or None
        # fields_data is nullable — None means "custom HTML draft" (clear the fields)
        if "fields_data" in kwargs:
            fd = kwargs["fields_data"]
            c.fields_data = fd if isinstance(fd, dict) else None
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

        # Send OTP via email.
        # html_escape() is applied to c.name because only html_content goes
        # through _sanitize_html() — the name field is a plain String(200) with
        # no HTML sanitisation.  Without escaping, a crafted name such as
        # '<img src=x onerror="...">' would be injected directly into the email.
        safe_name = html_escape(c.name)
        html_body = (
            f"<p>Your Trends.Earth Bulk Email verification code is:</p>"
            f"<h2 style='letter-spacing:0.2em'>{otp.token}</h2>"
            f"<p>This code expires in 15 minutes and is valid only for bulk email "
            f"<strong>{safe_name}</strong>.</p>"
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
    def send_bulk_email(bulk_email_id, user, code=None, recipient_list_id=None):
        """Send a bulk email.

        If recipient count > BULK_EMAIL_MAX_RECIPIENTS and code is None,
        raises VerificationRequiredError (HTTP 428).
        If code is supplied, it is verified before sending.
        """
        _check_approved_sender(user)

        # Use SELECT FOR UPDATE to atomically claim a DRAFT record.  This
        # prevents two concurrent POST /send requests from both passing the
        # status check and sending duplicate emails (race condition).
        c = (
            db.session.query(BulkEmail)
            .filter(BulkEmail.id == str(bulk_email_id))
            .with_for_update()
            .first()
        )
        if not c:
            raise BulkEmailNotFound(f"Bulk email {bulk_email_id!r} not found.")
        if c.status == "SENT":
            raise BulkEmailAlreadySent("Bulk email has already been sent.")

        # If the caller supplies a recipient list at send time, persist it on
        # the record so that resolve_recipient_count (and the send logic below)
        # both see the same value.
        if recipient_list_id:
            c.recipient_list_id = str(recipient_list_id)
            db.session.flush()

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

            # Filter by subscription type if set on this bulk email
            sub_type = c.subscription_type
            if sub_type == "news":
                users = [
                    u for u in users if getattr(u, "email_subscription_news", True)
                ]
            elif sub_type == "engagement":
                users = [
                    u
                    for u in users
                    if getattr(u, "email_subscription_engagement", True)
                ]
            elif sub_type == "system_updates":
                users = [
                    u
                    for u in users
                    if getattr(u, "email_subscription_system_updates", True)
                ]

            recipients = [
                {
                    "address": {"email": u.email, "name": u.name},
                    "substitution_data": {
                        "name": u.name,
                        "email": u.email,
                        "unsubscribe_footer": _unsubscribe_footer_html(u),
                    },
                }
                for u in users
            ]

        # Append the unsubscribe footer placeholder to the HTML at send time.
        # This is done *after* _sanitize_html() so the triple-brace SparkPost
        # substitution syntax is never passed through the sanitizer.
        send_html = (
            c.html_content + '\n<div style="text-align:center;padding:16px 0 8px;">'
            "{{{unsubscribe_footer}}}"
            "</div>"
        )

        try:
            for i in range(0, max(1, len(recipients)), _BATCH_SIZE):
                batch = recipients[i : i + _BATCH_SIZE]
                if not batch:
                    break
                EmailService.send_html_email(
                    recipients=batch,
                    html=send_html,
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
                "substitution_data": {
                    "name": u.name,
                    "email": u.email,
                    "unsubscribe_footer": _unsubscribe_footer_html(u),
                },
            }
            for u in superadmins
        ]

        test_subject = f"[TEST] {c.subject}"
        send_html = (
            c.html_content + '\n<div style="text-align:center;padding:16px 0 8px;">'
            "{{{unsubscribe_footer}}}"
            "</div>"
        )
        EmailService.send_html_email(
            recipients=recipients,
            html=send_html,
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
    def send_test_self_bulk_email(bulk_email_id, user):
        """Send bulk email as test only to the requesting user (no 2FA, no status change)."""  # noqa: E501
        _check_approved_sender(user)

        c = db.session.get(BulkEmail, str(bulk_email_id))
        if not c:
            raise BulkEmailNotFound(f"Bulk email {bulk_email_id!r} not found.")

        recipients = [
            {
                "address": {"email": user.email, "name": user.name},
                "substitution_data": {
                    "name": user.name,
                    "email": user.email,
                    "unsubscribe_footer": _unsubscribe_footer_html(user),
                },
            }
        ]

        test_subject = f"[TEST] {c.subject}"
        send_html = (
            c.html_content + '\n<div style="text-align:center;padding:16px 0 8px;">'
            "{{{unsubscribe_footer}}}"
            "</div>"
        )
        EmailService.send_html_email(
            recipients=recipients,
            html=send_html,
            from_email=_from_email(),
            subject=test_subject,
        )

        log_security_event(
            "BULK_EMAIL_TEST_SEND_SELF",
            user_id=str(user.id),
            details={"bulk_email_id": str(bulk_email_id)},
        )
        return {"sent_to": user.email}

    @staticmethod
    def get_config():
        """Return bulk email configuration for display in the UI."""
        return {
            "max_recipients": _max_recipients(),
            "from_email": _from_email(),
        }
