from __future__ import absolute_import, division, print_function

from flask import Blueprint, jsonify

# GENERIC Error


def error(status=400, detail="Bad Request"):
    return jsonify({"status": status, "detail": detail}), status


endpoints = Blueprint("endpoints", __name__)
import gefapi.routes.api.v1.gef_api_router  # noqa: E402, F401
