"""FLASK JWT METHODS"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import logging

from gefapi.services import UserService


def authenticate(email, password):
    logging.info("[JWT]: Auth user " + email)
    user = None
    try:
        user = UserService.authenticate_user(user_id=str(email), password=str(password))
    except Exception:
        logging.error("[JWT]: Error")
    return user


def identity(payload):
    user_id = str(payload["identity"])
    user = None
    try:
        user = UserService.get_user(user_id=user_id)
    except Exception as e:
        logging.error(str(e))
        logging.error("[JWT]: Error")
    return user
