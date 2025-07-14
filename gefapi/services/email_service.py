"""SCRIPT SERVICE"""

import logging
import os

import rollbar
from sparkpost import SparkPost
from gefapi.errors import EmailError

logger = logging.getLogger(__name__)


class EmailService:
    """MailService Class"""

    @staticmethod
    def send_html_email(
        recipients=None,
        html="",
        from_email="api@trends.earth",
        subject="[trends.earth] Undefined Subject",
    ):
        if recipients is None:
            recipients = []
        
        # Check if SparkPost API key is configured
        sparkpost_api_key = os.getenv("SPARKPOST_API_KEY")
        if not sparkpost_api_key:
            logger.warning(
                f"Cannot send email with subject '{subject}' to {len(recipients)} recipients: "
                "SPARKPOST_API_KEY is not configured. Email functionality is disabled."
            )
            # Return a mock response object to maintain compatibility
            return {"errors": ["Email disabled: SPARKPOST_API_KEY not configured"]}
            
        logger.debug(f"Sending email with subject {subject}")
        try:
            sp = SparkPost()
            response = sp.transmissions.send(
                recipients=recipients, html=html, from_email=from_email, subject=subject
            )

            return response
        except Exception as error:
            logger.error(
                f"Failed to send email with subject '{subject}': {error}. "
                "This may be due to missing or invalid SPARKPOST_API_KEY."
            )
            rollbar.report_exc_info()
            logger.exception(error)
            # Re-raise as EmailError to maintain compatibility with existing error handling
            raise EmailError(f"Failed to send email: {error}")
