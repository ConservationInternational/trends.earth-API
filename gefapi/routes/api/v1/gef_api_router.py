import logging
import os
import tempfile

import dateutil.parser
from flask import Response, json, jsonify, request, send_from_directory
from flask_jwt_extended import current_user, jwt_required

from gefapi.errors import (
    EmailError,
    ExecutionNotFound,
    InvalidFile,
    NotAllowed,
    ScriptDuplicated,
    ScriptNotFound,
    ScriptStateNotValid,
    UserDuplicated,
    UserNotFound,
)
from gefapi.routes.api.v1 import endpoints, error
from gefapi.s3 import get_script_from_s3
from gefapi.services import ExecutionService, ScriptService, StatusService, UserService
from gefapi.validators import (
    validate_execution_log_creation,
    validate_execution_update,
    validate_file,
    validate_user_creation,
    validate_user_update,
)

logger = logging.getLogger()


# SCRIPT CREATION
@endpoints.route("/script", strict_slashes=False, methods=["POST"])
@jwt_required()
@validate_file
def create_script():
    """
    Create a new script
    """
    logger.info("[ROUTER]: Creating a script")
    sent_file = request.files.get("file")
    if sent_file.filename == "":
        sent_file.filename = "script"
    user = current_user
    try:
        user = ScriptService.create_script(sent_file, user)
    except InvalidFile as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=400, detail=e.message)
    except ScriptDuplicated as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=400, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route("/script", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_scripts():
    """Get all scripts"""
    logger.info("[ROUTER]: Getting all scripts")

    # Parse query parameters
    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    filter_param = request.args.get("filter", None)
    sort = request.args.get("sort", None)

    # Pagination parameters - only paginate if user requests it
    page_param = request.args.get("page", None)
    per_page_param = request.args.get("per_page", None)

    if page_param is not None or per_page_param is not None:
        try:
            page = int(page_param) if page_param is not None else 1
            per_page = int(per_page_param) if per_page_param is not None else 20
            page = max(page, 1)
            per_page = min(max(per_page, 1), 100)
            paginate = True
        except Exception:
            page, per_page = 1, 20
            paginate = True
    else:
        page, per_page = 1, 2000
        paginate = False

    try:
        scripts, total = ScriptService.get_scripts(
            current_user,
            filter_param=filter_param,
            sort=sort,
            page=page,
            per_page=per_page,
            paginate=paginate,
        )
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")

    response_data = {
        "data": [script.serialize(include, exclude, current_user) for script in scripts]
    }
    if paginate:
        response_data["page"] = page
        response_data["per_page"] = per_page
        response_data["total"] = total

    return jsonify(response_data), 200


@endpoints.route("/script/<script>", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_script(script):
    """Get a script"""
    logger.info("[ROUTER]: Getting script " + script)
    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    try:
        script = ScriptService.get_script(script, current_user)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=script.serialize(include, exclude, current_user)), 200


@endpoints.route("/script/<script>/publish", strict_slashes=False, methods=["POST"])
@jwt_required()
def publish_script(script):
    """Publish a script"""
    logger.info("[ROUTER]: Publishing script " + script)
    try:
        script = ScriptService.publish_script(script, current_user)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=script.serialize(user=current_user)), 200


@endpoints.route("/script/<script>/unpublish", strict_slashes=False, methods=["POST"])
@jwt_required()
def unpublish_script(script):
    """Unpublish a script"""
    logger.info("[ROUTER]: Unpublishsing script " + script)
    try:
        script = ScriptService.unpublish_script(script, current_user)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=script.serialize(user=current_user)), 200


@endpoints.route("/script/<script>/download", strict_slashes=False, methods=["GET"])
@jwt_required()
def download_script(script):
    """Download a script"""
    logger.info("[ROUTER]: Download script " + script)
    try:
        script = ScriptService.get_script(script, current_user)

        temp_dir = tempfile.TemporaryDirectory().name
        script_file = script.slug + ".tar.gz"
        out_path = os.path.join(temp_dir, script_file)
        get_script_from_s3(script_file, out_path)

        return send_from_directory(directory=temp_dir, filename=script_file)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except NotAllowed as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=403, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")


@endpoints.route("/script/<script>/log", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_script_logs(script):
    """Get a script logs"""
    logger.info(f"[ROUTER]: Getting script logs of script {script} ")
    try:
        start = request.args.get("start", None)
        if start:
            start = dateutil.parser.parse(start)
        last_id = request.args.get("last-id", None)
        logs = ScriptService.get_script_logs(script, start, last_id)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=[log.serialize() for log in logs]), 200


@endpoints.route("/script/<script>", strict_slashes=False, methods=["PATCH"])
@jwt_required()
@validate_file
def update_script(script):
    """Update a script"""
    logger.info("[ROUTER]: Updating a script")
    sent_file = request.files.get("file")
    if sent_file.filename == "":
        sent_file.filename = "script"
    user = current_user
    # if user.role != 'ADMIN' and user.email != 'gef@gef.com':
    #     return error(status=403, detail='Forbidden')
    try:
        script = ScriptService.update_script(script, sent_file, user)
    except InvalidFile as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=400, detail=e.message)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except NotAllowed as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=403, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=script.serialize(user=current_user)), 200


@endpoints.route("/script/<script>", strict_slashes=False, methods=["DELETE"])
@jwt_required()
def delete_script(script):
    """Delete a script"""
    logger.info("[ROUTER]: Deleting script: " + script)
    identity = current_user
    if identity.role != "ADMIN" and identity.email != "gef@gef.com":
        return error(status=403, detail="Forbidden")
    try:
        script = ScriptService.delete_script(script, identity)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=script.serialize(user=current_user)), 200


# SCRIPT EXECUTION
@endpoints.route("/script/<script>/run", strict_slashes=False, methods=["POST"])
@jwt_required()
def run_script(script):
    """Run a script"""
    logger.info("[ROUTER]: Running script: " + script)
    user = current_user
    try:
        params = request.args.to_dict() if request.args else {}
        if request.get_json(silent=True):
            params.update(request.get_json())
        if "token" in params:
            del params["token"]
        execution = ExecutionService.create_execution(script, params, user)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except ScriptStateNotValid as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=400, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=execution.serialize(user=current_user)), 200


@endpoints.route("/execution", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_executions():
    """Get all executions"""
    logger.info("[ROUTER]: Getting all executions: ")
    user_id = request.args.get("user_id", None)
    updated_at = request.args.get("updated_at", None)
    if updated_at:
        updated_at = dateutil.parser.parse(updated_at)
    status = request.args.get("status", None)
    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    filter_param = request.args.get("filter", None)
    # Pagination parameters - only paginate if user requests it
    page_param = request.args.get("page", None)
    per_page_param = request.args.get("per_page", None)
    sort = request.args.get("sort", None)

    if page_param is not None or per_page_param is not None:
        # User requested pagination
        try:
            page = int(page_param) if page_param is not None else 1
            per_page = int(per_page_param) if per_page_param is not None else 20
            page = max(page, 1)
            per_page = min(max(per_page, 1), 100)
            paginate = True
        except Exception:
            page, per_page = 1, 20
            paginate = True
    else:
        # No pagination requested
        page, per_page = None, None
        paginate = False
    try:
        executions, total = ExecutionService.get_executions(
            current_user,
            user_id,
            updated_at,
            status=status,
            page=page,
            per_page=per_page,
            paginate=paginate,
            filter_param=filter_param,
            sort=sort,
        )
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")

    response_data = {
        "data": [
            execution.serialize(include, exclude, current_user)
            for execution in executions
        ]
    }
    # Only include pagination metadata if pagination was requested
    if paginate:
        response_data["page"] = page
        response_data["per_page"] = per_page
        response_data["total"] = total

    return jsonify(response_data), 200


@endpoints.route("/execution/<execution>", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_execution(execution):
    """Get an execution"""
    logger.info("[ROUTER]: Getting execution: " + execution)
    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    try:
        execution = ExecutionService.get_execution(execution, current_user)
    except ExecutionNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=execution.serialize(include, exclude, current_user)), 200


@endpoints.route("/execution/<execution>", strict_slashes=False, methods=["PATCH"])
@jwt_required()
@validate_execution_update
def update_execution(execution):
    """Update an execution"""
    logger.info("[ROUTER]: Updating execution " + execution)
    body = request.get_json()
    user = current_user
    if user.role != "ADMIN" and user.email != "gef@gef.com":
        return error(status=403, detail="Forbidden")
    try:
        execution = ExecutionService.update_execution(body, execution)
    except ExecutionNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=execution.serialize(user=current_user)), 200


@endpoints.route("/execution/<execution>/log", strict_slashes=False, methods=["GET"])
def get_execution_logs(execution):
    """Get the exectuion logs"""
    logger.info(f"[ROUTER]: Getting execution logs of execution {execution} ")
    try:
        start = request.args.get("start", None)
        if start:
            start = dateutil.parser.parse(start)
        last_id = request.args.get("last-id", None)
        logs = ExecutionService.get_execution_logs(execution, start, last_id)
    except ExecutionNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=[log.serialize() for log in logs]), 200


@endpoints.route(
    "/execution/<execution>/download-results", strict_slashes=False, methods=["GET"]
)
def get_download_results(execution):
    """Download results of the exectuion"""
    logger.info(f"[ROUTER]: Download execution results of execution {execution} ")
    try:
        execution = ExecutionService.get_execution(execution)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")

    return Response(
        json.dumps(execution.results),
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment;filename=results.json"},
    )


@endpoints.route("/execution/<execution>/log", strict_slashes=False, methods=["POST"])
@jwt_required()
@validate_execution_log_creation
def create_execution_log(execution):
    """Create log of an execution"""
    logger.info("[ROUTER]: Creating execution log for " + execution)
    body = request.get_json()
    user = current_user
    if user.role != "ADMIN" and user.email != "gef@gef.com":
        return error(status=403, detail="Forbidden")
    try:
        log = ExecutionService.create_execution_log(body, execution)
    except ExecutionNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=log.serialize()), 200


# USER
@endpoints.route("/user", strict_slashes=False, methods=["POST"])
@validate_user_creation
def create_user():
    """Create an user"""
    logger.info("[ROUTER]: Creating user")
    body = request.get_json()
    if request.headers.get("Authorization", None) is not None:
        logger.debug("[ROUTER]: Authorization header found")

        @jwt_required()
        def identity():
            pass

        identity()
    logger.debug("[ROUTER]: Getting identity")
    identity = current_user
    if identity:
        user_role = body.get("role", "USER")
        if identity.role == "USER" and user_role == "ADMIN":
            return error(status=403, detail="Forbidden")
    else:
        body["role"] = "USER"
    try:
        user = UserService.create_user(body)
    except UserDuplicated as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=400, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route("/user", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_users():
    """Get users"""
    logger.info("[ROUTER]: Getting all users")

    identity = current_user
    if identity.role != "ADMIN" and identity.email != "gef@gef.com":
        return error(status=403, detail="Forbidden")

    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    filter_param = request.args.get("filter", None)
    sort = request.args.get("sort", None)

    page_param = request.args.get("page", None)
    per_page_param = request.args.get("per_page", None)

    if page_param is not None or per_page_param is not None:
        try:
            page = int(page_param) if page_param is not None else 1
            per_page = int(per_page_param) if per_page_param is not None else 20
            page = max(page, 1)
            per_page = min(max(per_page, 1), 100)
            paginate = True
        except Exception:
            page, per_page = 1, 20
            paginate = True
    else:
        page, per_page = 1, 2000
        paginate = False

    try:
        users, total = UserService.get_users(
            filter_param=filter_param,
            sort=sort,
            page=page,
            per_page=per_page,
            paginate=paginate,
        )
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")

    response_data = {"data": [user.serialize(include, exclude) for user in users]}
    if paginate:
        response_data["page"] = page
        response_data["per_page"] = per_page
        response_data["total"] = total

    return jsonify(response_data), 200


@endpoints.route("/user/<user>", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_user(user):
    """Get an user"""
    logger.info("[ROUTER]: Getting user" + user)
    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    identity = current_user
    if identity.role != "ADMIN" and identity.email != "gef@gef.com":
        return error(status=403, detail="Forbidden")
    try:
        user = UserService.get_user(user)
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize(include, exclude)), 200


@endpoints.route("/user/me", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_me():
    """Get me"""
    logger.info("[ROUTER]: Getting my user")
    user = current_user
    return jsonify(data=user.serialize()), 200


@endpoints.route("/user/me", strict_slashes=False, methods=["PATCH"])
@jwt_required()
def update_profile():
    """Update an user"""
    logger.info("[ROUTER]: Updating profile")
    body = request.get_json()
    identity = current_user
    try:
        password = body.get("password", None)
        repeat_password = body.get("repeatPassword", None)
        if (
            password is not None
            and repeat_password is not None
            and password == repeat_password
        ):
            user = UserService.update_profile_password(body, identity)
        else:
            if "role" in body:
                del body["role"]
            name = body.get("name", None)
            country = body.get("country", None)
            institution = body.get("institution", None)
            if name is not None or country is not None or institution is not None:
                user = UserService.update_user(body, str(identity.id))
            else:
                return error(status=400, detail="Not updated")
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route("/user/me", strict_slashes=False, methods=["DELETE"])
@jwt_required()
def delete_profile():
    """Delete Me"""
    logger.info("[ROUTER]: Delete me")
    identity = current_user
    try:
        user = UserService.delete_user(str(identity.id))
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route(
    "/user/<user>/recover-password", strict_slashes=False, methods=["POST"]
)
def recover_password(user):
    """Revover password"""
    logger.info("[ROUTER]: Recovering password")
    try:
        user = UserService.recover_password(user)
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except EmailError as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=500, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route("/user/<user>", strict_slashes=False, methods=["PATCH"])
@jwt_required()
@validate_user_update
def update_user(user):
    """Update an user"""
    logger.info("[ROUTER]: Updating user" + user)
    body = request.get_json()
    identity = current_user
    if identity.role != "ADMIN" and identity.email != "gef@gef.com":
        return error(status=403, detail="Forbidden")
    try:
        user = UserService.update_user(body, user)
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route("/user/<user>", strict_slashes=False, methods=["DELETE"])
@jwt_required()
def delete_user(user):
    """Delete an user"""
    logger.info("[ROUTER]: Deleting user" + user)
    identity = current_user
    if user == "gef@gef.com":
        return error(status=403, detail="Forbidden")
    if identity.role != "ADMIN" and identity.email != "gef@gef.com":
        return error(status=403, detail="Forbidden")
    try:
        user = UserService.delete_user(user)
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route("/status", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_status_logs():
    """Get system status logs (Admin only)"""
    logger.info("[ROUTER]: Getting status logs")

    # Check if user is admin
    identity = current_user
    if identity.role != "ADMIN" and identity.email != "gef@gef.com":
        return error(status=403, detail="Forbidden")

    # Parse date filters
    start_date = request.args.get("start_date", None)
    if start_date:
        start_date = dateutil.parser.parse(start_date)

    end_date = request.args.get("end_date", None)
    if end_date:
        end_date = dateutil.parser.parse(end_date)

    # Parse sorting
    sort = request.args.get("sort", None)

    # Parse pagination
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 100))
        page = max(page, 1)
        per_page = min(max(per_page, 1), 1000)
    except Exception:
        page, per_page = 1, 100

    try:
        status_logs, total = StatusService.get_status_logs(
            start_date=start_date,
            end_date=end_date,
            sort=sort,
            page=page,
            per_page=per_page,
        )
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")

    return (
        jsonify(
            data=[status_log.serialize() for status_log in status_logs],
            page=page,
            per_page=per_page,
            total=total,
        ),
        200,
    )
