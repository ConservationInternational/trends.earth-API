"""GEFAPI SERVICES MODULE"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import sys
import logging

import rollbar
from rollbar.logger import RollbarHandler

# Ensure all unhandled exceptions are logged, and reported to rollbar
logger = logging.getLogger(__name__)
handler = logging.StreamHandler(stream=sys.stdout)
handler.setLevel(logging.INFO)
logger.addHandler(handler)

rollbar.init(os.getenv('ROLLBAR_SERVER_TOKEN'), os.getenv('ENV'))
rollbar_handler = RollbarHandler()
rollbar_handler.setLevel(logging.ERROR)
logger.addHandler(rollbar_handler)

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
sys.excepthook = handle_exception

from gefapi.services.docker_service import DockerService, docker_build, docker_run
from gefapi.services.email_service import EmailService
from gefapi.services.script_service import ScriptService
from gefapi.services.user_service import UserService
from gefapi.services.execution_service import ExecutionService
