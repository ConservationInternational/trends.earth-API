"""AUTH SERVICE"""

import logging

from gefapi.models import User

logger = logging.getLogger()


class AuthService(object):
    """User Class"""

    @staticmethod
    def auth(username, password):
        logger.info("[SERVICE]: Authorizing user " + username)
        logger.info("[DB]: QUERY")
        return User.query.filter_by(email=username, password=password).first()
