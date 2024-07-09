from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from flask import Blueprint

v2_endpoints = Blueprint('v2_endpoints', __name__)
import gefapi.routes.api.v2.gef_api_router  # noqa: autoimport
