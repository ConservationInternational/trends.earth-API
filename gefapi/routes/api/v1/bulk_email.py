"""Bulk Email routes for the Trends.Earth API.

All routes require SUPERADMIN role and that the caller's email is listed
in BULK_EMAIL_APPROVED_SENDERS (checked inside the service layer).
"""

import logging

from flask import jsonify, request
from flask_jwt_extended import current_user, jwt_required

from gefapi import limiter
from gefapi.errors import (
    AuthError,
    BulkEmailAlreadySent,
    BulkEmailNotFound,
    NotApprovedSender,
    RecipientListNotFound,
    VerificationRequiredError,
)
from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.bulk_email_service import BulkEmailService
from gefapi.utils.permissions import is_superadmin
from gefapi.utils.rate_limiting import (
    RateLimitConfig,
    get_non_exempt_key,
    is_rate_limiting_disabled,
)

logger = logging.getLogger(__name__)


def _require_superadmin(user):
    """Return a 403 response dict if user is not a superadmin, else None."""
    if not is_superadmin(user):
        return error(403, "Superadmin privileges required.")
    return None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@endpoints.route("/bulk-email/config", methods=["GET"])
@jwt_required()
def get_bulk_email_config():
    """Return bulk email config (max_recipients, from_email) for superadmins."""
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    cfg = BulkEmailService.get_config()
    return jsonify({"data": cfg}), 200


# ---------------------------------------------------------------------------
# Recipient Lists
# ---------------------------------------------------------------------------


@endpoints.route("/bulk-email/recipient-list", methods=["GET"])
@jwt_required()
def list_recipient_lists():
    """List all saved recipient lists."""
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    lists = BulkEmailService.list_recipient_lists()
    return jsonify(
        {
            "data": [
                {
                    "id": str(rl.id),
                    "name": rl.name,
                    "description": rl.description,
                    "filter_criteria": rl.filter_criteria,
                    "estimated_count": rl.estimated_count,
                    "created_at": rl.created_at.isoformat() if rl.created_at else None,
                    "created_by": (rl.created_by.email if rl.created_by else None),
                }
                for rl in lists
            ]
        }
    ), 200


@endpoints.route("/bulk-email/recipient-list", methods=["POST"])
@jwt_required()
def create_recipient_list():
    """Create a named recipient list from filter criteria."""
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    body = request.get_json(force=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return error(400, "name is required.")
    description = body.get("description")
    filter_criteria = body.get("filter_criteria", {})
    try:
        rl = BulkEmailService.create_recipient_list(
            name=name,
            description=description,
            filter_criteria=filter_criteria,
            created_by_id=str(current_user.id),
        )
    except Exception as exc:
        logger.exception("Error creating recipient list")
        return error(500, str(exc))
    return jsonify(
        {
            "data": {
                "id": str(rl.id),
                "name": rl.name,
                "description": rl.description,
                "filter_criteria": rl.filter_criteria,
                "estimated_count": rl.estimated_count,
            }
        }
    ), 201


@endpoints.route("/bulk-email/recipient-list/preview", methods=["POST"])
@jwt_required()
def preview_recipients():
    """Preview users matching filter criteria without saving."""
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    body = request.get_json(force=True) or {}
    filter_criteria = body.get("filter_criteria", {})
    limit = min(int(body.get("limit", 20)), 100)
    result = BulkEmailService.preview_recipients(filter_criteria, limit=limit)
    return jsonify({"data": result}), 200


@endpoints.route("/bulk-email/recipient-list/<list_id>", methods=["DELETE"])
@jwt_required()
def delete_recipient_list(list_id):
    """Delete a saved recipient list."""
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    try:
        BulkEmailService.delete_recipient_list(list_id)
    except RecipientListNotFound as exc:
        return error(404, exc.message)
    return jsonify({"message": "Recipient list deleted."}), 200


# ---------------------------------------------------------------------------
# Bulk Emails
# ---------------------------------------------------------------------------


@endpoints.route("/bulk-email", methods=["GET"])
@jwt_required()
def list_bulk_emails():
    """List bulk emails, optionally filtered by status."""
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    status = request.args.get("status")
    bulk_emails = BulkEmailService.list_bulk_emails(status=status)
    return jsonify({"data": [_serialize_bulk_email(c) for c in bulk_emails]}), 200


@endpoints.route("/bulk-email", methods=["POST"])
@jwt_required()
def create_bulk_email():
    """Create a draft bulk email."""
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    body = request.get_json(force=True) or {}
    name = body.get("name", "").strip()
    subject = body.get("subject", "").strip()
    html_content = body.get("html_content", "").strip()
    if not name or not subject or not html_content:
        return error(400, "name, subject, and html_content are required.")
    recipient_list_id = body.get("recipient_list_id")
    try:
        c = BulkEmailService.create_bulk_email(
            name=name,
            subject=subject,
            html_content=html_content,
            created_by_id=str(current_user.id),
            recipient_list_id=recipient_list_id,
        )
    except Exception as exc:
        logger.exception("Error creating bulk email")
        return error(500, str(exc))
    return jsonify({"data": _serialize_bulk_email(c)}), 201


@endpoints.route("/bulk-email/<bulk_email_id>", methods=["GET"])
@jwt_required()
def get_bulk_email(bulk_email_id):
    """Retrieve a single bulk email by ID."""
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    try:
        c = BulkEmailService.get_bulk_email(bulk_email_id)
    except BulkEmailNotFound as exc:
        return error(404, exc.message)
    return jsonify({"data": _serialize_bulk_email(c)}), 200


@endpoints.route("/bulk-email/<bulk_email_id>", methods=["PATCH"])
@jwt_required()
def update_bulk_email(bulk_email_id):
    """Update a draft bulk email."""
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    body = request.get_json(force=True) or {}
    try:
        c = BulkEmailService.update_bulk_email(
            bulk_email_id=bulk_email_id,
            user_id=str(current_user.id),
            **{
                k: body[k]
                for k in ("name", "subject", "html_content", "recipient_list_id")
                if k in body
            },
        )
    except BulkEmailNotFound as exc:
        return error(404, exc.message)
    except BulkEmailAlreadySent as exc:
        return error(409, exc.message)
    return jsonify({"data": _serialize_bulk_email(c)}), 200


@endpoints.route("/bulk-email/<bulk_email_id>", methods=["DELETE"])
@jwt_required()
def delete_bulk_email(bulk_email_id):
    """Delete a draft bulk email."""
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    try:
        BulkEmailService.delete_bulk_email(bulk_email_id)
    except BulkEmailNotFound as exc:
        return error(404, exc.message)
    except BulkEmailAlreadySent as exc:
        return error(409, exc.message)
    return jsonify({"message": "Bulk email deleted."}), 200


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@endpoints.route("/bulk-email/<bulk_email_id>/send-verification", methods=["POST"])
@limiter.limit(
    lambda: (
        ";".join(RateLimitConfig.get_bulk_email_verification_limits()) or "10 per hour"
    ),
    key_func=get_non_exempt_key,
    exempt_when=is_rate_limiting_disabled,
)
@jwt_required()
def send_verification(bulk_email_id):
    """Generate and email a 6-digit OTP to the caller for this bulk email."""
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    try:
        BulkEmailService.generate_verification_code(
            user_id=str(current_user.id), bulk_email_id=bulk_email_id
        )
    except (BulkEmailNotFound, NotApprovedSender) as exc:
        return error(404 if isinstance(exc, BulkEmailNotFound) else 403, exc.message)
    return jsonify({"message": "Verification code sent."}), 200


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


@endpoints.route("/bulk-email/<bulk_email_id>/send", methods=["POST"])
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_bulk_email_send_limits()) or "10 per hour",
    key_func=get_non_exempt_key,
    exempt_when=is_rate_limiting_disabled,
)
@jwt_required()
def send_bulk_email(bulk_email_id):
    """Send a bulk email.

    Optional body: {"code": "123456"} when responding to a 428 challenge.
    Returns 428 with {requires_verification, recipient_count} when threshold exceeded.
    """
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    body = request.get_json(force=True, silent=True) or {}
    code = body.get("code")
    try:
        c = BulkEmailService.send_bulk_email(
            bulk_email_id=bulk_email_id, user=current_user, code=code
        )
    except VerificationRequiredError as exc:
        return jsonify(exc.serialize), 428
    except NotApprovedSender as exc:
        return error(403, exc.message)
    except BulkEmailNotFound as exc:
        return error(404, exc.message)
    except BulkEmailAlreadySent as exc:
        return error(409, exc.message)
    except AuthError as exc:
        return error(400, exc.message)
    return jsonify({"data": _serialize_bulk_email(c)}), 200


@endpoints.route("/bulk-email/<bulk_email_id>/send-test", methods=["POST"])
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_bulk_email_send_limits()) or "10 per hour",
    key_func=get_non_exempt_key,
    exempt_when=is_rate_limiting_disabled,
)
@jwt_required()
def send_test_bulk_email(bulk_email_id):
    """Send bulk email as test to superadmins only (no 2FA, status unchanged)."""
    guard = _require_superadmin(current_user)
    if guard:
        return guard
    try:
        result = BulkEmailService.send_test_bulk_email(
            bulk_email_id=bulk_email_id, user=current_user
        )
    except NotApprovedSender as exc:
        return error(403, exc.message)
    except BulkEmailNotFound as exc:
        return error(404, exc.message)
    return jsonify({"data": result}), 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_bulk_email(c):
    return {
        "id": str(c.id),
        "name": c.name,
        "subject": c.subject,
        "html_content": c.html_content,
        "status": c.status,
        "recipient_list_id": str(c.recipient_list_id) if c.recipient_list_id else None,
        "recipient_count": c.recipient_count,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "sent_at": c.sent_at.isoformat() if c.sent_at else None,
        "created_by": c.created_by.email if c.created_by else None,
        "sent_by": c.sent_by.email if c.sent_by else None,
    }
