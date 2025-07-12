"""SCRIPT SERVICE"""

import logging

import rollbar
from sparkpost import SparkPost

logger = logging.getLogger()


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
        logger.debug(f"Sending email with subject {subject}")
        try:
            sp = SparkPost()
            response = sp.transmissions.send(
                recipients=recipients, html=html, from_email=from_email, subject=subject
            )

            return response
        except Exception as error:
            rollbar.report_exc_info()
            logger.exception(error)
