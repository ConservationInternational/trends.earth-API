from flask import Blueprint, jsonify

# GENERIC Error


def error(status=400, detail="Bad Request"):
    return jsonify({"status": status, "detail": detail}), status


endpoints = Blueprint("endpoints", __name__)
import gefapi.routes.api.v1.gef_api_router  # noqa: E402, F401
import gefapi.routes.api.v1.google_groups  # noqa: E402, F401
import gefapi.routes.api.v1.script_access  # noqa: E402, F401
