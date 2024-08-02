"""The GEF API MODULE"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import logging
import os
import sys

from flask import current_app
from flask import Flask
from flask import got_request_exception
from flask import request
from flask_cors import CORS
from flask_compress import Compress
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

import rollbar
import rollbar.contrib.flask
# from rollbar.logger import RollbarHandler

from gefapi.celery import make_celery
from gefapi.config import SETTINGS


# Flask App
app = Flask(__name__)
CORS(app)
Compress(app)

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# logging.basicConfig(
#    level=logging.DEBUG,
#    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
#    datefmt="%Y%m%d-%H:%M%p",
# )

# Ensure all unhandled exceptions are logged, and reported to rollbar
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler = logging.StreamHandler(stream=sys.stdout)
handler.setLevel(logging.INFO)
handler.setFormatter(formatter)
logger.addHandler(handler)

rollbar.init(os.getenv("ROLLBAR_SERVER_TOKEN"), os.getenv("ENV"))

# rollbar_handler = RollbarHandler()
# rollbar_handler.setLevel(logging.ERROR)
# logger.addHandler(rollbar_handler)


with app.app_context():
    got_request_exception.connect(rollbar.contrib.flask.report_exception, app)


def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = handle_exception

# Config

app.config["SQLALCHEMY_DATABASE_URI"] = SETTINGS.get("SQLALCHEMY_DATABASE_URI")
app.config["SECRET_KEY"] = SETTINGS.get("SECRET_KEY")
app.config["UPLOAD_FOLDER"] = SETTINGS.get("UPLOAD_FOLDER")
app.config["JWT_AUTH_USERNAME_KEY"] = SETTINGS.get("JWT_AUTH_USERNAME_KEY")
app.config["JWT_AUTH_HEADER_PREFIX"] = SETTINGS.get("JWT_AUTH_HEADER_PREFIX")
app.config["JWT_EXPIRATION_DELTA"] = SETTINGS.get("JWT_EXPIRATION_DELTA")
app.config["broker_url"] = SETTINGS.get("CELERY_BROKER_URL")
app.config["result_backend"] = SETTINGS.get("CELERY_RESULT_BACKEND")

# Database
db = SQLAlchemy(app)

migrate = Migrate(app, db)

celery = make_celery(app)

# DB has to be ready!
from gefapi.routes.api.v1 import endpoints, error  # noqa: E402

# Blueprint Flask Routing
app.register_blueprint(endpoints, url_prefix="/api/v1")

from flask_jwt import JWT  # noqa: E402

from gefapi.jwt import authenticate, identity  # noqa: E402

# JWT
jwt = JWT(app, authenticate, identity)


class JWTError(Exception):
    def __init__(self, error, description, status_code=401, headers=None):
        self.error = error
        self.description = description
        self.status_code = status_code
        self.headers = headers

    def __repr__(self):
        return "JWTError: %s" % self.error

    def __str__(self):
        return "%s. %s" % (self.error, self.description)


@jwt.request_handler
def request_handler():
    auth_header_value = request.headers.get("Authorization", None)
    auth_header_prefix = current_app.config["JWT_AUTH_HEADER_PREFIX"]

    if auth_header_value is None and request.args.get("token", None) is not None:
        logger.info(request.args.get("token", ""))
        auth_header_value = auth_header_prefix + " " + request.args.get("token", "")

    if auth_header_value is None:
        return None

    parts = auth_header_value.split()

    if parts[0].lower() != auth_header_prefix.lower():
        raise JWTError("Invalid JWT header", "Unsupported authorization type")
    elif len(parts) == 1:
        raise JWTError("Invalid JWT header", "Token missing")
    elif len(parts) > 2:
        raise JWTError("Invalid JWT header", "Token contains spaces")

    return parts[1]


@app.errorhandler(403)
def forbidden(e):
    return error(status=403, detail="Forbidden")


@app.errorhandler(404)
def page_not_found(e):
    return error(status=404, detail="Not Found")


@app.errorhandler(405)
def method_not_allowed(e):
    return error(status=405, detail="Method Not Allowed")


@app.errorhandler(410)
def gone(e):
    return error(status=410, detail="Gone")


@app.errorhandler(500)
def internal_server_error(e):
    return error(status=500, detail="Internal Server Error")
