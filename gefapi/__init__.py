"""The GEF API MODULE"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import json
import logging

from flask import Flask, request, current_app
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS, cross_origin
from gefapi.config import SETTINGS
from gefapi.celery import make_celery
from flask_jwt import JWTError

logging.basicConfig(
    level=SETTINGS.get('logging', {}).get('level'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y%m%d-%H:%M%p',
)


# Flask App
app = Flask(__name__)
CORS(app)

# Config
app.config['SQLALCHEMY_DATABASE_URI'] = SETTINGS.get('SQLALCHEMY_DATABASE_URI')
app.config['SECRET_KEY'] = SETTINGS.get('SECRET_KEY')
app.config['UPLOAD_FOLDER'] = SETTINGS.get('UPLOAD_FOLDER')
app.config['JWT_AUTH_USERNAME_KEY'] = SETTINGS.get('JWT_AUTH_USERNAME_KEY')
app.config['JWT_AUTH_HEADER_PREFIX'] = SETTINGS.get('JWT_AUTH_HEADER_PREFIX')
app.config['JWT_EXPIRATION_DELTA'] = SETTINGS.get('JWT_EXPIRATION_DELTA')
app.config['CELERY_BROKER_URL'] = SETTINGS.get('CELERY_BROKER_URL')
app.config['CELERY_RESULT_BACKEND'] = SETTINGS.get('CELERY_RESULT_BACKEND')
app.config['CORS_HEADERS'] = 'Content-Type'

# Database
db = SQLAlchemy(app)

migrate = Migrate(app, db)

celery = make_celery(app)

# DB has to be ready!
from gefapi.routes.api.v1 import endpoints, error
# Blueprint Flask Routing
app.register_blueprint(endpoints, url_prefix='/api/v1')

from flask_jwt import JWT
from gefapi.jwt import authenticate, identity
# JWT
jwt = JWT(app, authenticate, identity)


@jwt.request_handler
def request_handler():
    auth_header_value = request.headers.get('Authorization', None)
    auth_header_prefix = current_app.config['JWT_AUTH_HEADER_PREFIX']

    if auth_header_value is None and request.args.get('token', None) is not None:
        logging.info(request.args.get('token', ''))
        auth_header_value = auth_header_prefix + ' ' + request.args.get('token', '')

    if auth_header_value is None:
        return None

    parts = auth_header_value.split()

    if parts[0].lower() != auth_header_prefix.lower():
        raise JWTError('Invalid JWT header', 'Unsupported authorization type')
    elif len(parts) == 1:
        raise JWTError('Invalid JWT header', 'Token missing')
    elif len(parts) > 2:
        raise JWTError('Invalid JWT header', 'Token contains spaces')

    return parts[1]


@app.errorhandler(403)
def forbidden(e):
    return error(status=403, detail='Forbidden')


@app.errorhandler(404)
def page_not_found(e):
    return error(status=404, detail='Not Found')


@app.errorhandler(405)
def method_not_allowed(e):
    return error(status=405, detail='Method Not Allowed')


@app.errorhandler(410)
def gone(e):
    return error(status=410, detail='Gone')


@app.errorhandler(500)
def internal_server_error(e):
    return error(status=500, detail='Internal Server Error')
