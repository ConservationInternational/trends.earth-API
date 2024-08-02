"""SCRIPT SERVICE"""

from __future__ import absolute_import, division, print_function

import logging

import rollbar
from sparkpost import SparkPost

logger = logging.getLogger()


class EmailService(object):
    """MailService Class"""

    @staticmethod
    def send_html_email(
        recipients=[],
        html="",
        from_email="api@trends.earth",
        subject="[trends.earth] Undefined Subject",
    ):
        logger.debug("Sending email with subject %s" % (subject))
        try:
            sp = SparkPost()
            response = sp.transmissions.send(
                recipients=recipients, html=html, from_email=from_email, subject=subject
            )

            return response
        except Exception as error:
            rollbar.report_exc_info()
            logger.exception(error)
