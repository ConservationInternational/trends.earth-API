"""The GEF API MODULE"""

from __future__ import absolute_import, division, print_function

import logging
import os
import sys

import rollbar
import rollbar.contrib.flask
from flask import Flask, got_request_exception, jsonify, request
from flask_compress import Compress
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

# from rollbar.logger import RollbarHandler
from gefapi.celery import make_celery
from gefapi.config import SETTINGS

# Flask App
app = Flask(__name__)
CORS(app)
Compress(app)

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# Ensure all unhandled exceptions are logged, and reported to rollbar
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler = logging.StreamHandler(stream=sys.stdout)
handler.setLevel(logging.INFO)
handler.setFormatter(formatter)
logger.addHandler(handler)

rollbar.init(os.getenv("ROLLBAR_SERVER_TOKEN"), os.getenv("ENV"))
with app.app_context():
    got_request_exception.connect(rollbar.contrib.flask.report_exception, app)

app.config["SQLALCHEMY_DATABASE_URI"] = SETTINGS.get("SQLALCHEMY_DATABASE_URI")
app.config["UPLOAD_FOLDER"] = SETTINGS.get("UPLOAD_FOLDER")
app.config["JWT_SECRET_KEY"] = SETTINGS.get("SECRET_KEY")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = SETTINGS.get("JWT_ACCESS_TOKEN_EXPIRES")
app.config["JWT_QUERY_STRING_NAME"] = SETTINGS.get("JWT_QUERY_STRING_NAME")
app.config["JWT_TOKEN_LOCATION"] = SETTINGS.get("JWT_TOKEN_LOCATION")
app.config["broker_url"] = SETTINGS.get("CELERY_BROKER_URL")
app.config["result_backend"] = SETTINGS.get("CELERY_RESULT_BACKEND")

# Database
db = SQLAlchemy(app)

migrate = Migrate(app, db)

celery = make_celery(app)

# DB has to be ready!
# Import tasks to register them with Celery
from gefapi import tasks  # noqa: E402
from gefapi.routes.api.v1 import endpoints, error  # noqa: E402

# Blueprint Flask Routing
app.register_blueprint(endpoints, url_prefix="/api/v1")


# Handle authentication via JWT
jwt = JWTManager(app)


from gefapi.models import User  # noqa:E402
from gefapi.services import UserService  # noqa:E402


@app.route("/auth", methods=["POST"])
def create_token():
    logger.info("[JWT]: Attempting auth...")
    email = request.json.get("email", None)
    password = request.json.get("password", None)

    user = UserService.authenticate_user(email, password)

    if user is None:
        return jsonify({"msg": "Bad username or password"}), 401

    access_token = create_access_token(identity=user.id)
    return jsonify({"access_token": access_token, "user_id": user.id})


@jwt.user_lookup_loader
def user_lookup_callback(_jwt_header, jwt_data):
    identity = jwt_data["sub"]
    return User.query.filter_by(id=identity).one_or_none()


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
