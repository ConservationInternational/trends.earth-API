import logging

from flask import current_app, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt_identity

from gefapi.services import UserService

logger = logging.getLogger()


@current_app.route("/auth", methods=["POST"])
def create_token():
    logger.info("[JWT]: Attempting auth...")
    email = request.json.get("email", None)
    password = request.json.get("password", None)

    user = UserService.authenticate_user(email, password)

    if user is None:
        return jsonify({"msg": "Bad username or password"}), 401

    access_token = create_access_token(identity=user.id)
    return jsonify({"access_token": access_token, "user_id": user.id})


def get_identity():
    user = None
    try:
        id = get_jwt_identity()
        user = UserService.get_user(id)
    except Exception as e:
        logger.error(str(e))
        logger.error("[JWT]: Error getting user for %s" % (id))
    return user
