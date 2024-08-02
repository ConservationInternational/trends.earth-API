"""FLASK JWT METHODS"""

from __future__ import absolute_import, division, print_function

import logging

from gefapi.services import UserService

logger = logging.getLogger()


def authenticate(email, password):
    logger.info("[JWT]: Auth user " + email)
    user = None
    try:
        user = UserService.authenticate_user(user_id=str(email), password=str(password))
    except Exception:
        logger.error("[JWT]: Error")
    return user


def identity(payload):
    user_id = str(payload["identity"])
    user = None
    try:
        user = UserService.get_user(user_id=user_id)
    except Exception as e:
        logger.error(str(e))
        logger.error("[JWT]: Error")
    return user
