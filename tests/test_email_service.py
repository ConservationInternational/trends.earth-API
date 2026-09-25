"""Tests for the shared SparkPost email service."""

from email.utils import parseaddr
from unittest.mock import patch

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import BulkEmail, User
from gefapi.services.bulk_email_service import BulkEmailService
from gefapi.services.email_service import EmailService

_send_html_email = EmailService.send_html_email


@patch.dict("os.environ", {"SPARKPOST_API_KEY": "test-key"})
@patch("gefapi.services.email_service.SparkPost")
def test_send_html_email_sets_friendly_from_for_transactional_email(mock_sparkpost):
    _send_html_email(
        recipients=["user@example.com"],
        from_email="api@trends.earth",
        subject="Password reset",
        transactional=True,
    )

    send_kwargs = mock_sparkpost.return_value.transmissions.send.call_args.kwargs
    assert parseaddr(send_kwargs["from_email"]) == (
        "Trends.Earth",
        "api@trends.earth",
    )
    assert send_kwargs["transactional"] is True


@patch.dict("os.environ", {"SPARKPOST_API_KEY": "test-key"})
@patch("gefapi.services.email_service.SparkPost")
def test_send_html_email_sets_friendly_from_for_bulk_email(mock_sparkpost):
    _send_html_email(
        recipients=["user@example.com"],
        from_email="noreply@trends.earth",
        subject="News",
    )

    sender = mock_sparkpost.return_value.transmissions.send.call_args.kwargs[
        "from_email"
    ]
    assert parseaddr(sender) == ("Trends.Earth", "noreply@trends.earth")


@patch("gefapi.services.bulk_email_service.EmailService.send_html_email")
def test_bulk_email_verification_code_is_transactional(mock_send, app):
    with app.app_context():
        user = User(
            email="approved-sender@example.com",
            password="ValidPass123!",
            name="Approved Sender",
            country="Test Country",
            institution="Test Institution",
            role="SUPERADMIN",
        )
        db.session.add(user)
        db.session.flush()
        bulk_email = BulkEmail(
            name="Verification Test",
            subject="Test",
            html_content="<p>Test</p>",
            created_by_id=user.id,
        )
        db.session.add(bulk_email)
        db.session.commit()

        with patch.dict(
            SETTINGS,
            {"BULK_EMAIL_APPROVED_SENDERS": [user.email]},
        ):
            BulkEmailService.generate_verification_code(user.id, bulk_email.id)

        assert mock_send.call_args.kwargs["transactional"] is True
