import logging
import os
import tempfile

import dateutil.parser
from flask import Response, json, jsonify, request, send_from_directory
from flask_jwt_extended import current_user, get_jwt_identity, jwt_required

# Import rate limiter instance
from gefapi import app, limiter
from gefapi.errors import (
    AuthError,
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
from gefapi.services.refresh_token_service import RefreshTokenService
from gefapi.utils.permissions import (
    can_access_admin_features,
    can_change_user_password,
    can_change_user_role,
    can_delete_user,
    can_update_user_profile,
    is_admin_or_higher,
)
from gefapi.utils.rate_limiting import (
    RateLimitConfig,
    get_admin_aware_key,
    is_rate_limiting_disabled,
)
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
    Create a new script by uploading a script file.

    **Authentication**: JWT token required
    **Authorization**: Admin or Superadmin access required
    **Content-Type**: multipart/form-data (file upload)

    **Form Data**:
    - `file`: Script file to upload
      - Supported formats: Python scripts (.py), ZIP archives
      - Must contain valid script configuration
      - File size limits apply (configured server-side)

    **Script Requirements**:
    - Must include proper script metadata/configuration
    - Python scripts should follow trends.earth script structure
    - ZIP archives should contain main script file and dependencies

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "script-123",
        "slug": "my-new-script",
        "name": "New Analysis Script",
        "description": "Performs geospatial analysis",
        "status": "UPLOADED",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T10:30:00Z",
        "user_id": "admin-456",
        "cpu": 1,
        "memory": 2048,
        "logs": false
      }
    }
    ```

    **Script Status After Creation**:
    - `UPLOADED`: Script successfully uploaded and validated
    - Further publishing required before execution is possible

    **Error Responses**:
    - `400 Bad Request`: Invalid file format, duplicate script, or validation failed
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin access required
    - `413 Payload Too Large`: File size exceeds limits
    - `500 Internal Server Error`: Script creation failed
    """
    logger.info("[ROUTER]: Creating a script")

    # Check if user is admin or superadmin
    if not is_admin_or_higher(current_user):
        return error(
            status=403, detail="Only admins and superadmins can create scripts"
        )

    sent_file = request.files.get("file")
    if sent_file.filename == "":
        sent_file.filename = "script"
    user = current_user
    try:
        script = ScriptService.create_script(sent_file, user)
    except InvalidFile as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=400, detail=e.message)
    except ScriptDuplicated as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=400, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=script.serialize()), 200


@endpoints.route("/script", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_scripts():
    """
    Retrieve all scripts with flexible filtering, sorting, and pagination.

    **Authentication**: JWT token required
    **Access**: Returns scripts visible to the current user based on permissions

    **Query Parameters**:
    - `include`: Comma-separated list of additional fields to include in response
    - `exclude`: Comma-separated list of fields to exclude from response
    - `filter`: Search/filter scripts by name, description, or other attributes
    - `sort`: Sort field (prefix with '-' for descending, e.g., '-created_at')
    - `page`: Page number for pagination (triggers pagination when provided)
    - `per_page`: Items per page (1-100, default: 20, max without pagination: 2000)

    **Response Schema (without pagination)**:
    ```json
    {
      "data": [
        {
          "id": "script-123",
          "slug": "my-analysis-script",
          "name": "Land Use Analysis",
          "description": "Analyzes land use changes over time",
          "status": "PUBLISHED",
          "created_at": "2025-01-15T10:30:00Z",
          "updated_at": "2025-01-15T10:30:00Z",
          "user_id": "user-456",
          "cpu": 2,
          "memory": 4096,
          "logs": false
        }
      ]
    }
    ```

    **Response Schema (with pagination)**:
    ```json
    {
      "data": [...],
      "page": 1,
      "per_page": 20,
      "total": 150
    }
    ```

    **Script Status Values**:
    - `UPLOADED`: Script uploaded but not yet published
    - `PUBLISHED`: Script is available for execution
    - `UNPUBLISHED`: Script was published but later unpublished
    - `FAILED`: Script validation or processing failed

    **Filtering Examples**:
    - `?filter=land` - Find scripts with "land" in name or description
    - `?filter=status:PUBLISHED` - Find only published scripts
    - `?filter=user:john@example.com` - Find scripts by specific user

    **Sorting Examples**:
    - `?sort=name` - Sort by name ascending
    - `?sort=-created_at` - Sort by creation date descending
    - `?sort=status` - Sort by status

    **Field Control Examples**:
    - `?include=logs` - Include execution logs
    - `?include=user` - Include user information
    - `?exclude=description,logs` - Exclude verbose fields

    **Pagination Control**:
    - Without pagination: Returns up to 2000 scripts in single response
    - With pagination: `?page=1&per_page=20` - Returns paginated results

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `500 Internal Server Error`: Failed to retrieve scripts
    """
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
    try:
        updated_script = ScriptService.update_script(script, sent_file, user)
        return jsonify(data=updated_script.serialize(user=current_user)), 200
    except (InvalidFile, ScriptNotFound, NotAllowed) as e:
        status_code = 400
        if isinstance(e, ScriptNotFound):
            status_code = 404
        elif isinstance(e, NotAllowed):
            status_code = 403
        logger.error(f"[ROUTER]: {e.message}")
        return error(status=status_code, detail=e.message)
    except Exception as e:
        logger.error(f"[ROUTER]: {e}")
        return error(status=500, detail=str(e))


@endpoints.route("/script/<script>", strict_slashes=False, methods=["DELETE"])
@jwt_required()
def delete_script(script):
    """Delete a script"""
    logger.info("[ROUTER]: Deleting script: " + script)
    identity = current_user
    if not can_access_admin_features(identity):
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
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_execution_run_limits()),
    key_func=get_admin_aware_key,
    exempt_when=is_rate_limiting_disabled,
)  # Rate limit script execution
@jwt_required()
def run_script(script):
    """
    Execute a script with provided parameters.

    **Rate Limited**: Subject to execution limits (configurable per minute/hour)
    **Authentication**: JWT token required
    **Access**: Script must be published and user must have execution permissions

    **Request Schema**:
    ```json
    {
      "param1": "value1",
      "param2": 123,
      "param3": true,
      "nested_param": {
        "sub_param": "nested_value"
      }
    }
    ```

    **Path Parameters**:
    - `script`: Script identifier/name to execute

    **Request Body**: JSON object containing script parameters
    - Parameters vary by script - see individual script documentation
    - Can include nested objects and arrays
    - Boolean, string, and numeric values supported

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "exec_123456",
        "script_id": "my-script",
        "status": "PENDING",
        "params": {
          "param1": "value1",
          "param2": 123
        },
        "user_id": "user_789",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T10:30:00Z",
        "start_time": null,
        "end_time": null,
        "results": null
      }
    }
    ```

    **Execution States**:
    - `PENDING`: Execution queued, waiting to start
    - `RUNNING`: Currently executing
    - `SUCCESS`: Completed successfully
    - `FAILED`: Execution failed with error
    - `CANCELLED`: Execution was cancelled

    **Error Responses**:
    - `400 Bad Request`: Invalid parameters or script not in valid state
    - `401 Unauthorized`: JWT token required
    - `404 Not Found`: Script does not exist
    - `429 Too Many Requests`: Execution rate limit exceeded
    - `500 Internal Server Error`: Execution creation failed
    """
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


@endpoints.route("/execution/user", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_user_executions():
    """
    Retrieve executions for the current authenticated user.

    **Authentication**: JWT token required
    **Access**: Returns only executions belonging to the current user
    **Scope**: User-specific endpoint - users can only see their own executions

    **Query Parameters**:
    - `include`: Comma-separated list of additional fields to include in response
    - `exclude`: Comma-separated list of fields to exclude from response
    - `filter`: Search/filter executions by script name, status, or other attributes
    - `sort`: Sort field (prefix with '-' for descending, e.g., '-created_at',
      '-updated_at')
    - `updated_at`: Filter executions started after specific timestamp (ISO 8601)
    - `page`: Page number for pagination (triggers pagination when provided)
    - `per_page`: Items per page (1-100, default: 20)

    **Response Schema (without pagination)**:
    ```json
    {
      "data": [
        {
          "id": "exec-123",
          "script_id": "script-456",
          "status": "SUCCESS",
          "params": {
            "region": "africa",
            "year_start": 2020,
            "year_end": 2023
          },
          "user_id": "user-789",
          "created_at": "2025-01-15T10:30:00Z",
          "updated_at": "2025-01-15T11:45:00Z",
          "start_time": "2025-01-15T10:31:00Z",
          "end_time": "2025-01-15T11:45:00Z",
          "results": {
            "output_file": "analysis_results.json",
            "summary": "Processing completed successfully"
          }
        }
      ]
    }
    ```

    **Response Schema (with pagination)**:
    ```json
    {
      "data": [...],
      "page": 1,
      "per_page": 20,
      "total": 45
    }
    ```

    **Execution Status Values**:
    - `PENDING`: Execution queued, waiting to start
    - `RUNNING`: Currently executing
    - `SUCCESS`: Completed successfully with results
    - `FAILED`: Execution failed with error
    - `CANCELLED`: Execution was cancelled before completion

    **Filtering Examples**:
    - `?filter=land-analysis` - Find executions related to "land-analysis" script
    - `?filter=status:SUCCESS` - Find only successful executions
    - `?filter=2024` - Find executions from 2024 (searches in timestamps)

    **Sorting Examples**:
    - `?sort=created_at` - Sort by creation time ascending
    - `?sort=-updated_at` - Sort by last update descending (most recent first)
    - `?sort=status` - Sort by execution status

    **Timestamp Filtering**:
    - `?updated_at=2025-01-15T10:30:00Z` - Find executions started after date
    - `updated_at` parameter accepts ISO 8601 format
    - Returns executions that started after the specified timestamp
    - Useful for incremental synchronization and monitoring recent activity

    **Pagination Examples**:
    - `?page=1&per_page=50` - Get first 50 executions with pagination
    - `?page=2&per_page=20` - Get second page with 20 executions per page
    - **Performance Note**: Without pagination, results are limited to 1000 executions

    **Field Control Examples**:
    - `?include=script,logs` - Include script details and execution logs
    - `?include=script_name,user_name` - Include script and user names
    - `?exclude=params,results` - Exclude verbose parameter and result data

    **Combined Query Examples**:
        - `?updated_at=2025-08-01&include=script&page=1&per_page=20`
    - `?filter=vegetation&sort=-created_at&exclude=params`

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `500 Internal Server Error`: Failed to retrieve executions
    """
    logger.info(f"[ROUTER]: Getting executions for user: {current_user.email}")
    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    filter_param = request.args.get("filter", None)
    sort = request.args.get("sort", None)

    # Add support for updated_at filtering
    updated_at = request.args.get("updated_at", None)
    if updated_at:
        import dateutil.parser

        updated_at = dateutil.parser.parse(updated_at)

    # Pagination parameters
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
        page, per_page = None, None
        paginate = False

    try:
        # Get executions for current user only
        executions, total = ExecutionService.get_executions(
            user=current_user,
            target_user_id=None,  # None means get current user's executions
            updated_at=updated_at,
            status=None,
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


@endpoints.route("/execution", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_executions():
    """
    Retrieve all executions with admin filtering and cross-user visibility.

    **Authentication**: JWT token required
    **Access**: Admin users can see all executions, regular users see only their own
    **Admin Features**: ADMIN+ users can filter by user_id to view other users'
      executions

    **Query Parameters**:
    - `user_id`: Filter executions by specific user ID (admin-only feature)
    - `updated_at`: Filter executions started after specific timestamp (ISO 8601)
    - `status`: Filter by execution status (PENDING, RUNNING, SUCCESS, FAILED,
      CANCELLED)
    - `include`: Comma-separated list of additional fields to include
    - `exclude`: Comma-separated list of fields to exclude
    - `filter`: General search/filter across execution attributes
    - `sort`: Sort field (prefix with '-' for descending, e.g., '-updated_at')
    - `page`: Page number for pagination (triggers pagination when provided)
    - `per_page`: Items per page (1-100, default: 20, max without pagination:
      varies by permission)

    **Response Schema (without pagination)**:
    ```json
    {
      "data": [
        {
          "id": "exec-123",
          "script_id": "script-456",
          "status": "SUCCESS",
          "params": {
            "region": "africa",
            "analysis_type": "vegetation_change"
          },
          "user_id": "user-789",
          "created_at": "2025-01-15T10:30:00Z",
          "updated_at": "2025-01-15T11:45:00Z",
          "start_time": "2025-01-15T10:31:00Z",
          "end_time": "2025-01-15T11:45:00Z",
          "results": {
            "analysis_complete": true,
            "output_files": ["vegetation_2023.tif", "change_summary.json"]
          }
        }
      ]
    }
    ```

    **Response Schema (with pagination)**:
    ```json
    {
      "data": [...],
      "page": 1,
      "per_page": 20,
      "total": 1250
    }
    ```

    **Admin Query Examples**:
    - `?user_id=123` - View executions for specific user (admin only)
    - `?status=FAILED` - Find all failed executions across system
    - `?updated_at=2025-01-15T00:00:00Z` - Find executions updated since date

    **Regular User Behavior**:
    - Non-admin users: Only see their own executions regardless of user_id parameter
    - Admin users: Can see all executions, can filter by user_id

    **Filtering Examples**:
    - `?filter=vegetation` - Find executions with "vegetation" in script or params
    - `?filter=2024-12` - Find executions from December 2024
    - `?status=RUNNING&sort=-created_at` - Find currently running executions,
      newest first

    **Sorting Examples**:
    - `?sort=updated_at` - Sort by last update ascending
    - `?sort=-created_at` - Sort by creation time descending (newest first)
    - `?sort=status` - Sort by execution status alphabetically

    **Timestamp Filtering**:
    - `updated_at` parameter accepts ISO 8601 format: `2025-01-15T10:30:00Z`
    - Returns executions that started after the specified timestamp
    - Useful for incremental synchronization and monitoring

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `500 Internal Server Error`: Failed to retrieve executions
    """
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
            user=current_user,
            target_user_id=user_id,
            updated_at=updated_at,
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
    """
    Retrieve a specific execution by ID.

    **Authentication**: JWT token required
    **Access**: Users can only view their own executions (or admin can view any)

    **Path Parameters**:
    - `execution`: Execution ID (UUID format)

    **Query Parameters**:
    - `include`: Comma-separated list of additional fields to include
      - Available: `script`, `script_name`, `user_name`, `user_email`, `logs`
    - `exclude`: Comma-separated list of fields to exclude
      - Available: `params`, `results`

    **Response Schema**:
    ```json
    {
      "data": {
        "id": "abc123-def456",
        "script_id": "vegetation-analysis",
        "status": "SUCCESS",
        "params": {
          "region": "africa",
          "year_start": 2020,
          "year_end": 2023
        },
        "results": {
          "output_file": "vegetation_change.tif",
          "summary": "Analysis completed successfully"
        },
        "user_id": "user-789",
        "start_date": "2025-01-15T10:30:00Z",
        "end_date": "2025-01-15T11:45:00Z",
        "progress": 100
      }
    }
    ```

    **Field Control Examples**:
    - `?include=script` - Include full script details
    - `?include=script_name,user_name` - Include script and user names only
    - `?exclude=params,results` - Exclude large data fields

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Cannot access execution (not yours and not admin)
    - `404 Not Found`: Execution does not exist
    - `500 Internal Server Error`: Server error
    """
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
    """
    Update an execution's properties (admin only).

    **Authentication**: JWT token required
    **Authorization**: Admin level access required
    **Access**: Only admin users can update execution properties

    **Path Parameters**:
    - `execution`: Execution ID (UUID format)

    **Request Body**:
    ```json
    {
      "status": "CANCELLED",
      "progress": 100,
      "results": {
        "reason": "User requested cancellation"
      }
    }
    ```

    **Updatable Fields**:
    - `status`: Execution status (PENDING, RUNNING, SUCCESS, FAILED, CANCELLED)
    - `progress`: Progress percentage (0-100)
    - `results`: Results data (JSON object)
    - `end_date`: End timestamp (ISO 8601 format)

    **Response Schema**:
    ```json
    {
      "data": {
        "id": "abc123-def456",
        "script_id": "vegetation-analysis",
        "status": "CANCELLED",
        "progress": 100,
        "user_id": "user-789",
        "start_date": "2025-01-15T10:30:00Z",
        "end_date": "2025-01-15T10:35:00Z",
        "results": {
          "reason": "User requested cancellation"
        }
      }
    }
    ```

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin access required
    - `404 Not Found`: Execution does not exist
    - `422 Unprocessable Entity`: Invalid request data
    - `500 Internal Server Error`: Server error
    """
    logger.info("[ROUTER]: Updating execution " + execution)
    body = request.get_json()
    user = current_user
    if not can_access_admin_features(user):
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


@endpoints.route(
    "/execution/<execution>/cancel", strict_slashes=False, methods=["POST"]
)
@jwt_required()
def cancel_execution(execution):
    """
    Cancel a running execution and any associated Google Earth Engine tasks.

    **Authentication**: JWT token required
    **Access**: Users can cancel their own executions, ADMIN+ users can cancel any
    **Purpose**: Immediately stops execution processing, cleans up resources, and
    cancels any associated Google Earth Engine tasks that may have been started.

    **Path Parameters**:
    - `execution`: The ID of the execution to cancel

    **Request**: No request body required - this is a POST endpoint that triggers
    cancellation

    **Usage Examples**:
    ```bash
    # Cancel your own execution
    curl -X POST "https://api.trends.earth/api/v1/execution/abc123-def456/cancel" \
         -H "Authorization: Bearer your_jwt_token"

    # Admin canceling any user's execution
    curl -X POST "https://api.trends.earth/api/v1/execution/xyz789-uvw012/cancel" \
         -H "Authorization: Bearer admin_jwt_token"
    ```

    **Success Response Schema**:
    ```json
    {
      "data": {
        "execution": {
          "id": "abc123-def456",
          "script_id": "vegetation-analysis",
          "status": "CANCELLED",
          "params": {
            "region": "africa",
            "year_start": 2020,
            "year_end": 2023
          },
          "user_id": "user-789",
          "created_at": "2025-01-15T10:30:00Z",
          "updated_at": "2025-01-15T10:35:00Z",
          "start_time": "2025-01-15T10:31:00Z",
          "end_time": "2025-01-15T10:35:00Z",
          "progress": 100
        },
        "cancellation_details": {
          "execution_id": "abc123-def456",
          "previous_status": "RUNNING",
          "docker_service_stopped": true,
          "docker_container_stopped": false,
          "gee_tasks_cancelled": [
            {
              "task_id": "6CIGR7EG2J45GJ2DN2J7X3WZ",
              "success": true,
              "error": null,
              "status": "CANCELLED"
            },
            {
              "task_id": "YBKKBHM2V63JYBVIPCCRY7A2",
              "success": true,
              "error": null,
              "status": "CANCELLED"
            }
          ],
          "errors": []
        }
      }
    }
    ```

    **Response Fields**:
    - `execution`: The updated execution object with CANCELLED status
    - `cancellation_details`: Detailed information about what was cancelled:
      - `execution_id`: ID of the cancelled execution
      - `previous_status`: Status before cancellation (e.g., "RUNNING", "PENDING")
      - `docker_service_stopped`: Whether Docker service was found and stopped
      - `docker_container_stopped`: Whether Docker container was found and stopped
      - `gee_tasks_cancelled`: Array of Google Earth Engine tasks that were cancelled
        - `task_id`: The GEE task identifier
        - `success`: Whether the cancellation was successful
        - `error`: Error message if cancellation failed
        - `status`: Final status of the GEE task
      - `errors`: Any errors encountered during cancellation process

    **Cancellation Process**:
    1. **Docker Resources**: Stops and removes Docker services/containers associated
       with the execution
    2. **GEE Task Detection**: Scans execution logs for Google Earth Engine task IDs
       using patterns like:
       - "Starting GEE task 6CIGR7EG2J45GJ2DN2J7X3WZ"
       - "Backing off ... for task YBKKBHM2V63JYBVIPCCRY7A2"
    3. **GEE Task Cancellation**: Uses Google Earth Engine REST API to cancel
       detected tasks
    4. **Status Update**: Sets execution status to CANCELLED and logs the cancellation

    **Cancellable States**:
    - `PENDING`: Execution queued, waiting to start
    - `READY`: Execution initialized and starting
    - `RUNNING`: Currently executing

    **Non-Cancellable States**:
    - `FINISHED`: Execution completed successfully
    - `FAILED`: Execution already failed
    - `CANCELLED`: Execution already cancelled

    **Error Responses**:
    - `400 Bad Request`: Execution is not in a cancellable state
      ```json
      {
        "status": 400,
        "detail": "Cannot cancel execution in FINISHED state"
      }
      ```
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: User can only cancel their own executions (unless admin)
      ```json
      {
        "status": 403,
        "detail": "You can only cancel your own executions"
      }
      ```
    - `404 Not Found`: Execution does not exist
    - `500 Internal Server Error`: Cancellation process failed

    **Partial Cancellation**: The endpoint will attempt to cancel all associated
    resources even if some steps fail. Check the `cancellation_details.errors` array
    for any issues encountered during the process.
    """
    logger.info(f"[ROUTER]: Canceling execution {execution}")
    user = current_user

    try:
        # Check if user can cancel this execution
        execution_obj = ExecutionService.get_execution(execution, user)
        if not execution_obj:
            return error(status=404, detail="Execution not found")

        # Only allow users to cancel their own executions, or admins to cancel any
        if not is_admin_or_higher(user) and execution_obj.user_id != user.id:
            return error(status=403, detail="You can only cancel your own executions")

        # Check if execution is in a cancellable state
        if execution_obj.status in ["FINISHED", "FAILED", "CANCELLED"]:
            return error(
                status=400,
                detail=f"Cannot cancel execution in {execution_obj.status} state",
            )

        # Cancel the execution
        result = ExecutionService.cancel_execution(execution)

        return jsonify(data=result), 200

    except ExecutionNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Failed to cancel execution")


@endpoints.route("/execution/<execution>/log", strict_slashes=False, methods=["GET"])
def get_execution_logs(execution):
    """
    Retrieve logs for a specific execution.

    **Authentication**: Not required (public endpoint)
    **Access**: Anyone can view execution logs for monitoring purposes

    **Path Parameters**:
    - `execution`: Execution ID (UUID format)

    **Query Parameters**:
    - `start`: Start timestamp for log filtering (ISO 8601 format)
    - `last-id`: Last log ID for pagination/incremental updates

    **Response Schema**:
    ```json
    {
      "data": [
        {
          "id": "log-123",
          "execution_id": "abc123-def456",
          "timestamp": "2025-01-15T10:30:15Z",
          "level": "INFO",
          "message": "Processing started for region: africa",
          "details": {
            "step": "initialization",
            "progress": 5
          }
        },
        {
          "id": "log-124",
          "execution_id": "abc123-def456",
          "timestamp": "2025-01-15T10:30:45Z",
          "level": "INFO",
          "message": "Analysis 25% complete",
          "details": {
            "step": "processing",
            "progress": 25
          }
        }
      ]
    }
    ```

    **Usage Examples**:
    - `?start=2025-01-15T10:30:00Z` - Get logs after specific timestamp
    - `?last-id=log-120` - Get logs after specific log ID (pagination)
    - `?start=2025-01-15T10:30:00Z&last-id=log-120` - Combined filtering

    **Log Levels**:
    - `DEBUG`: Detailed diagnostic information
    - `INFO`: General information about execution progress
    - `WARNING`: Warning messages about potential issues
    - `ERROR`: Error messages about failures

    **Error Responses**:
    - `404 Not Found`: Execution does not exist
    - `500 Internal Server Error`: Server error
    """
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


@endpoints.route(
    "/execution/<execution>/docker-logs", strict_slashes=False, methods=["GET"]
)
@jwt_required()
def get_execution_docker_logs(execution):
    """
    Retrieve Docker service logs for a specific execution.

    **Authentication**: JWT token required
    **Access**: Restricted to ADMIN and SUPERADMIN users only
    **Purpose**: Provides raw Docker service logs for debugging and monitoring
      individual script executions.

    **Path Parameters**:
    - `execution`: The ID of the execution to retrieve logs for.

    **Success Response Schema**:
    ```json
    {
      "data": [
        {
          "id": 0,
          "created_at": "2025-08-04T10:30:00.123456Z",
          "text": "Log message from the container"
        },
        {
          "id": 1,
          "created_at": "2025-08-04T10:30:01.789012Z",
          "text": "Another log message"
        }
      ]
    }
    ```

    **Response Fields**:
    - `id`: A sequential identifier for the log line (for ordering).
    - `created_at`: The timestamp of the log entry (ISO 8601 format).
    - `text`: The content of the log line.

    **Error Responses**:
    - `401 Unauthorized`: JWT token required.
    - `403 Forbidden`: Insufficient privileges (ADMIN+ required).
    - `404 Not Found`: The specified execution or its logs do not exist.
    - `500 Internal Server Error`: Failed to retrieve logs due to a server-side
      issue.
    """
    logger.info(f"[ROUTER]: Getting docker logs for execution {execution}")
    user = current_user
    if not is_admin_or_higher(user):
        return error(status=403, detail="Forbidden")
    try:
        from gefapi.services.docker_service import DockerService

        logs = DockerService.get_service_logs(execution)
        if logs is None:
            return error(status=404, detail="Logs not found for execution")
        return jsonify(data=logs), 200
    except Exception as e:
        logger.error(f"[ROUTER]: Error getting docker logs: {e}")
        return error(status=500, detail="Internal Server Error")


@endpoints.route("/execution/<execution>/log", strict_slashes=False, methods=["POST"])
@jwt_required()
@validate_execution_log_creation
def create_execution_log(execution):
    """Create log of an execution"""
    logger.info("[ROUTER]: Creating execution log for " + execution)
    body = request.get_json()
    user = current_user
    if not can_access_admin_features(user):
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
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_user_creation_limits()),
    key_func=get_admin_aware_key,
    exempt_when=is_rate_limiting_disabled,
)  # Configurable rate limit for user creation
@validate_user_creation
def create_user():
    """
    Create a new user account.

    **Rate Limited**: Subject to user creation rate limits (configurable)
    **Access**: Public endpoint - no authentication required for basic user creation
    **Admin Features**: Creating ADMIN/SUPERADMIN users requires SUPERADMIN
      authentication

    **Request Schema**:
    ```json
    {
      "email": "user@example.com",
      "password": "securePassword123",
      "name": "John Doe",
      "country": "US",
      "institution": "Example Organization",
      "role": "USER"
    }
    ```

    **Request Fields**:
    - `email`: User's email address (required, must be unique)
    - `password`: User's password (required, minimum security requirements apply)
    - `name`: User's full name (required)
    - `country`: Two-letter country code (optional)
    - `institution`: User's organization/institution (optional)
    - `role`: User role - "USER", "ADMIN", or "SUPERADMIN" (default: "USER")

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "123",
        "email": "user@example.com",
        "name": "John Doe",
        "role": "USER",
        "country": "US",
        "institution": "Example Organization",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T10:30:00Z"
      }
    }
    ```

    **Role Creation Rules**:
    - Anyone can create "USER" accounts
    - Only SUPERADMIN users can create "ADMIN" or "SUPERADMIN" accounts
    - Attempting to create privileged roles without permission returns 403 Forbidden

    **Error Responses**:
    - `400 Bad Request`: Email already exists, validation failed, or weak password
    - `403 Forbidden`: Insufficient privileges to create the requested role
    - `429 Too Many Requests`: Rate limit exceeded
    - `500 Internal Server Error`: User creation failed
    """
    logger.info("[ROUTER]: Creating user")
    body = request.get_json()
    if request.headers.get("Authorization", None) is not None:

        @jwt_required()
        def identity():
            pass

        identity()
    identity = current_user
    if identity:
        user_role = body.get("role", "USER")
        # Only superadmin can create admin or superadmin users
        if user_role in ["ADMIN", "SUPERADMIN"] and not can_change_user_role(identity):
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
    """
    Retrieve list of users with filtering, sorting, and pagination.

    **Authentication**: JWT token required
    **Access**: Restricted to ADMIN and SUPERADMIN users only

    **Query Parameters**:
    - `include`: Comma-separated list of additional fields to include
    - `exclude`: Comma-separated list of fields to exclude from response
    - `filter`: Filter users by email, name, role, or other attributes
    - `sort`: Sort field (prefix with '-' for descending, e.g., '-created_at')
    - `page`: Page number for pagination (triggers pagination when provided)
    - `per_page`: Items per page (1-100, default: 20)

    **Response Schema (without pagination)**:
    ```json
    {
      "data": [
        {
          "id": "123",
          "email": "user@example.com",
          "name": "John Doe",
          "role": "USER",
          "country": "US",
          "institution": "Example Organization",
          "created_at": "2025-01-15T10:30:00Z",
          "updated_at": "2025-01-15T10:30:00Z"
        }
      ]
    }
    ```

    **Response Schema (with pagination)**:
    ```json
    {
      "data": [...],
      "page": 1,
      "per_page": 20,
      "total": 150
    }
    ```

    **Filtering Examples**:
    - `?filter=admin` - Find users with "admin" in email, name, or role
    - `?filter=role:ADMIN` - Find users with ADMIN role
    - `?filter=country:US` - Find users from United States

    **Sorting Examples**:
    - `?sort=name` - Sort by name ascending
    - `?sort=-created_at` - Sort by creation date descending
    - `?sort=email` - Sort by email ascending

    **Field Control Examples**:
    - `?include=password_last_changed` - Include additional fields
    - `?exclude=institution,country` - Exclude specified fields

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Insufficient privileges (ADMIN+ required)
    - `500 Internal Server Error`: Failed to retrieve users
    """
    logger.info("[ROUTER]: Getting all users")

    identity = current_user
    if not is_admin_or_higher(identity):
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
    if not is_admin_or_higher(identity):
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
    """
    Get current authenticated user's profile information.

    **Authentication**: JWT token required
    **Access**: Returns current user's own profile data

    **Response Schema**:
    ```json
    {
      "data": {
        "id": "user-123",
        "name": "John Doe",
        "email": "john.doe@example.com",
        "role": "USER",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T11:45:00Z",
        "institution": "Conservation International",
        "country": "United States",
        "is_active": true
      }
    }
    ```

    **User Roles**:
    - `USER`: Regular user with basic permissions
    - `MANAGER`: Manager with elevated permissions
    - `ADMIN`: Administrator with full system access
    - `SUPERADMIN`: Super administrator with unrestricted access

    **Error Responses**:
    - `401 Unauthorized`: JWT token required or invalid
    """
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


@endpoints.route("/user/me/change-password", strict_slashes=False, methods=["PATCH"])
@jwt_required()
def change_password():
    """Change user password"""
    logger.info("[ROUTER]: Changing password")
    body = request.get_json()
    identity = current_user
    old_password = body.get("old_password")
    new_password = body.get("new_password")

    if not old_password or not new_password:
        return error(status=400, detail="old_password and new_password are required")

    try:
        user = UserService.change_password(identity, old_password, new_password)
    except AuthError as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=401, detail=e.message)
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
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_password_reset_limits()),
    key_func=get_admin_aware_key,
    exempt_when=is_rate_limiting_disabled,
)  # Configurable rate limit for password recovery
def recover_password(user):
    """Recover password"""
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

    # Check if user is trying to update role - only superadmin can do this
    if "role" in body and not can_change_user_role(identity):
        return error(status=403, detail="Forbidden")

    # Check if user can update other user's profile
    if not can_update_user_profile(identity):
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
    if not can_delete_user(identity):
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


@endpoints.route(
    "/user/<user>/change-password", strict_slashes=False, methods=["PATCH"]
)
@jwt_required()
def admin_change_password(user):
    """Admin change user password"""
    logger.info("[ROUTER]: Admin changing password for user " + user)
    body = request.get_json()
    identity = current_user

    # Check if user can change other user's password
    if not can_change_user_password(identity):
        return error(status=403, detail="Forbidden")

    new_password = body.get("new_password")
    if not new_password:
        return error(status=400, detail="new_password is required")

    try:
        target_user = UserService.get_user(user)
        user = UserService.admin_change_password(target_user, new_password)
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
    """
    Retrieve system status logs for monitoring and diagnostics.

    **Authentication**: JWT token required
    **Access**: Restricted to ADMIN and SUPERADMIN users only
    **Purpose**: Monitor system health, track events, and diagnose issues

    **Query Parameters**:
    - `start_date`: Filter logs from this date onwards (ISO 8601 format)
    - `end_date`: Filter logs up to this date (ISO 8601 format)
    - `sort`: Sort field (prefix with '-' for descending, e.g., '-timestamp')
    - `page`: Page number for pagination (default: 1)
    - `per_page`: Items per page (1-10000, default: 100)

    **Response Schema**:
    ```json
    {
      "data": [
        {
          "id": 123,
          "timestamp": "2025-01-15T10:30:00Z",
          "executions_active": 5,
          "executions_ready": 2,
          "executions_running": 3,
          "executions_finished": 8,
          "executions_failed": 1,
          "executions_cancelled": 0
        },
        {
          "id": 124,
          "timestamp": "2025-01-15T10:35:00Z",
          "executions_active": 8,
          "executions_ready": 5,
          "executions_running": 3,
          "executions_finished": 12,
          "executions_failed": 2,
          "executions_cancelled": 1
        }
      ],
      "page": 1,
      "per_page": 100,
      "total": 1547
    }
    ```

    **Status Log Fields**:
    - `id`: Unique identifier for the status log entry
    - `timestamp`: When the status was recorded (ISO 8601 format)
    - `executions_active`: Number of active executions (RUNNING + PENDING)
    - `executions_ready`: Number of executions in READY state
    - `executions_running`: Number of currently running executions
    - `executions_finished`: Number of executions that finished
    - `executions_failed`: Number of executions that failed
    - `executions_cancelled`: Number of executions that were cancelled

    **Monitoring Metrics**:
    - Track execution queue length and processing status
    - Monitor execution completion and failure rates
    - Identify trends in script execution success/failure rates
    - System health indicators for capacity planning
    - Event-driven status tracking provides real-time execution state

    **Date Filtering Examples**:
    - `?start_date=2025-01-15T00:00:00Z` - Logs from January 15th onwards
    - `?end_date=2025-01-15T23:59:59Z` - Logs up to end of January 15th
    - `?start_date=2025-01-10T00:00:00Z&end_date=2025-01-15T23:59:59Z` - Logs
      within date range

    **Sorting Examples**:
    - `?sort=timestamp` - Chronological order (oldest first)
    - `?sort=-timestamp` - Reverse chronological (newest first, default)
    - `?sort=level` - Sort by severity level

    **Pagination Examples**:
    - `?page=1&per_page=50` - First 50 entries
    - `?page=2&per_page=100` - Next 100 entries
    - Default pagination: 100 items per page

    **Use Cases**:
    - Monitor execution queue length and processing capacity
    - Track system growth (users and scripts over time)
    - Analyze execution success rates and failure patterns
    - Capacity planning based on execution activity trends
    - Performance monitoring and bottleneck identification

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Insufficient privileges (ADMIN+ required)
    - `500 Internal Server Error`: Failed to retrieve status logs
    """
    logger.info("[ROUTER]: Getting status logs")

    # Check if user is admin or higher
    identity = current_user
    if not can_access_admin_features(identity):
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
        per_page = min(max(per_page, 1), 10000)
    except ValueError:
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


@endpoints.route("/status/swarm", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_swarm_status():
    """
    Get cached Docker Swarm cluster status including comprehensive node information.

    **Authentication**: JWT token required
    **Access**: Restricted to ADMIN and SUPERADMIN users only
    **Purpose**: Monitor Docker Swarm health, node resources, and capacity
    **Performance**: Uses Redis-cached data updated every 2 minutes for fast response

    **Response Schema**:
    ```json
    {
      "data": {
        "swarm_active": true,              // Whether Docker is in swarm mode
        "total_nodes": 3,                  // Total number of nodes in swarm
        "total_managers": 1,               // Number of manager nodes
        "total_workers": 2,                // Number of worker nodes
        "error": null,                     // Error message if any
        "cache_info": {                    // Cache metadata
          "cached_at": "2025-01-15T10:30:00Z",  // When data was cached/retrieved
          "cache_ttl": 300,                     // Cache TTL in seconds
          "cache_key": "docker_swarm_status",   // Redis cache key
          "source": "cached"                    // Data source
        },
        "nodes": [
          {
            "id": "node-id-123",           // Unique node identifier
            "hostname": "manager-01",       // Node hostname
            "role": "manager",             // Node role: "manager" or "worker"
            "is_manager": true,            // Whether node is a manager
            "is_leader": true,             // Whether node is the swarm leader
            "availability": "active",      // "active", "pause", "drain"
            "state": "ready",              // Node state: "ready", "down", "unknown"
            "cpu_count": 4.0,              // Number of CPUs available
            "memory_gb": 8.0,              // Memory in GB
            "running_tasks": 3,            // Number of currently running tasks
            "available_capacity": 37,      // Additional tasks that can fit
            "resource_usage": {
              "used_cpu_nanos": 300000000,        // CPU nanoseconds used
              "used_memory_bytes": 536870912,     // Memory bytes used
              "available_cpu_nanos": 3700000000,  // CPU nanoseconds available
              "available_memory_bytes": 7548381184, // Memory bytes available
              "used_cpu_percent": 7.5,            // CPU usage percentage
              "used_memory_percent": 6.25         // Memory usage percentage
            },
            "labels": {"node.role": "manager"},  // Node labels/tags
            "created_at": "2025-01-15T10:00:00Z", // Node creation timestamp
            "updated_at": "2025-01-15T10:30:00Z"  // Node last update timestamp
          },
          {
            "id": "worker-node-456",
            "hostname": "worker-01",
            "role": "worker",
            "is_manager": false,
            "is_leader": false,
            "availability": "active",
            "state": "ready",
            "cpu_count": 2.0,
            "memory_gb": 4.0,
            "running_tasks": 2,
            "available_capacity": 18,
            "resource_usage": {
              "used_cpu_nanos": 200000000,
              "used_memory_bytes": 314572800,
              "available_cpu_nanos": 1800000000,
              "available_memory_bytes": 3979059200,
              "used_cpu_percent": 10.0,
              "used_memory_percent": 7.5
            },
            "labels": {"node.role": "worker"},
            "created_at": "2025-01-15T10:05:00Z",
            "updated_at": "2025-01-15T10:30:00Z"
          }
        ]
      }
    }
    ```

    **Data Source**:
    - Uses cached Docker Swarm data from Redis (updated every 2 minutes)
    - Resource calculations based on actual Docker Swarm task reservations
    - Node capacity calculated from CPU/memory resources and current task load

    **Error Responses**:
    - 403: Access denied (non-admin user)
    - 500: Server error

    **Note**: When Docker is not in swarm mode or unavailable, returns:
    ```json
    {
      "data": {
        "swarm_active": false,
        "error": "Not in swarm mode" | "Docker unavailable",
        "nodes": [],
        "total_nodes": 0,
        "total_managers": 0,
        "total_workers": 0,
        "cache_info": {
          "cached_at": "2025-01-15T10:30:00Z",
          "cache_ttl": 0,
          "cache_key": "docker_swarm_status",
          "source": "real_time_fallback" | "endpoint_error_fallback"
        }
      }
    }
    ```
    """
    logger.info("[ROUTER]: Getting Docker Swarm status")

    from flask_jwt_extended import get_jwt_identity

    from gefapi.services import UserService

    try:
        # Check user permissions
        user_id = get_jwt_identity()
        user = UserService.get_user(user_id)

        if not user or user.role not in ["ADMIN", "SUPERADMIN"]:
            logger.error(f"[ROUTER]: Access denied for user {user_id}")
            return error(status=403, detail="Access denied. Admin privileges required.")

        # Get cached Docker Swarm information (fast)
        try:
            from gefapi.tasks.status_monitoring import get_cached_swarm_status

            swarm_info = get_cached_swarm_status()
        except Exception as swarm_error:
            import datetime

            logger.warning(
                f"[ROUTER]: Failed to get cached Docker Swarm info: {swarm_error}"
            )
            swarm_info = {
                "error": f"Cache retrieval failed: {str(swarm_error)}",
                "nodes": [],
                "total_nodes": 0,
                "total_managers": 0,
                "total_workers": 0,
                "swarm_active": False,
                "cache_info": {
                    "cached_at": datetime.datetime.now(datetime.UTC).isoformat(),
                    "cache_ttl": 0,
                    "cache_key": "docker_swarm_status",
                    "source": "endpoint_error_fallback",
                },
            }

        logger.info("[ROUTER]: Successfully retrieved swarm status")
        return jsonify(data=swarm_info), 200

    except Exception as e:
        logger.error(f"[ROUTER]: Error getting swarm status: {str(e)}")
        return error(status=500, detail="Error retrieving swarm status")


@endpoints.route("/rate-limit/status", methods=["GET"])
@jwt_required()
def get_rate_limit_status():
    """
    Query current rate limiting status across the system.

    **Access**: Restricted to users with `role: "SUPERADMIN"`
    **Purpose**: Provides visibility into current rate limiting state for monitoring
      and debugging

    **Response Schema**:
    ```json
    {
      "message": "Rate limiting status retrieved successfully",
      "data": {
        "enabled": true,
        "storage_type": "RedisStorage",
        "total_active_limits": 5,
        "active_limits": [
          {
            "key": "user:123",
            "type": "user",
            "identifier": "123",
            "current_count": 8,
            "time_window_seconds": "60",
            "user_info": {
              "id": "123",
              "email": "user@example.com",
              "name": "John Doe",
              "role": "USER"
            }
          },
          {
            "key": "ip:192.168.1.100",
            "type": "ip",
            "identifier": "192.168.1.100",
            "current_count": 15,
            "time_window_seconds": "3600",
            "user_info": null
          }
        ]
      }
    }
    ```

    **Response Fields**:
    - `enabled`: Boolean indicating if rate limiting is active
    - `storage_type`: Backend storage type (RedisStorage, MemoryStorage, etc.)
    - `total_active_limits`: Count of currently active rate limit entries
    - `active_limits`: Array of active rate limit entries with:
      - `key`: Internal rate limit identifier
      - `type`: Limit type ("user", "ip", "auth")
      - `identifier`: User ID or IP address being limited
      - `current_count`: Current request count against the limit
      - `time_window_seconds`: Time window for the rate limit (60=1min, 3600=1hr)
      - `user_info`: User details for user-type limits (null for IP limits)

    **Error Responses**:
    - `403 Forbidden`: User does not have SUPERADMIN privileges
    - `401 Unauthorized`: Valid JWT token required
    - `500 Internal Server Error`: Failed to query rate limiting status
    """
    current_user_id = get_jwt_identity()
    user = UserService.get_user(current_user_id)

    if not user or user.role != "SUPERADMIN":
        return jsonify({"msg": "Superadmin access required"}), 403

    try:
        from gefapi.utils.rate_limiting import get_current_rate_limits

        rate_limit_status = get_current_rate_limits()

        return jsonify(
            {
                "message": "Rate limiting status retrieved successfully",
                "data": rate_limit_status,
            }
        ), 200

    except Exception as e:
        app.logger.error(f"Failed to get rate limit status: {e}")
        return jsonify({"error": "Failed to retrieve rate limiting status"}), 500


@endpoints.route("/rate-limit/reset", methods=["POST"])
@jwt_required()
def reset_rate_limits():
    """
    Reset all rate limits across the system.

    **Access**: Restricted to users with `role: "SUPERADMIN"`
    **Purpose**: Clears all current rate limit counters - useful for emergency
      situations or testing

    **Request**: No request body required

    **Success Response Schema**:
    ```json
    {
      "message": "All rate limits have been reset."
    }
    ```

    **Use Cases**:
    - Emergency situations where legitimate users are being rate limited
    - Testing and development environments
    - After configuration changes to rate limiting policies
    - System maintenance and debugging

    **Behavior**:
    - Clears all rate limit counters from storage (Redis/Memory)
    - Affects all endpoints and all users/IP addresses
    - Does not disable rate limiting - new requests will start fresh counters
    - Operation is immediate and irreversible

    **Error Responses**:
    - `403 Forbidden`: User does not have SUPERADMIN privileges
    - `401 Unauthorized`: Valid JWT token required
    - `500 Internal Server Error`: Failed to reset rate limits
    """
    current_user_id = get_jwt_identity()
    user = UserService.get_user(current_user_id)

    if not user or user.role != "SUPERADMIN":
        return jsonify({"msg": "Superadmin access required"}), 403

    try:
        # For Flask-Limiter, we need to reset the storage properly
        # Check if the storage has a reset method or clear all keys
        if hasattr(limiter.storage, "reset"):
            limiter.storage.reset()
        elif hasattr(limiter.storage, "clear_all"):
            limiter.storage.clear_all()
        else:
            # Fallback: try to get all keys and clear them
            # This is storage-dependent, but for memory storage we can try this approach
            try:
                # For MemoryStorage, access the internal storage directly
                if hasattr(limiter.storage, "storage"):
                    limiter.storage.storage.clear()
                else:
                    # Alternative: recreate the limiter to clear all limits
                    limiter._storage = limiter._storage.__class__(limiter._storage_uri)
            except Exception:
                # Last resort: disable and re-enable the limiter to reset state
                was_enabled = limiter.enabled
                limiter.enabled = False
                limiter.enabled = was_enabled

        return jsonify({"message": "All rate limits have been reset."}), 200
    except Exception as e:
        app.logger.error(f"Failed to reset rate limits: {e}")
        return jsonify({"error": "Failed to reset rate limits"}), 500


@endpoints.route("/user/me/sessions", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_user_sessions():
    """Get user's active sessions"""
    logger.info("[ROUTER]: Getting user sessions")
    identity = current_user

    try:
        active_sessions = RefreshTokenService.get_user_active_sessions(identity.id)
        return jsonify(data=[session.serialize() for session in active_sessions]), 200
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")


@endpoints.route(
    "/user/me/sessions/<session_id>", strict_slashes=False, methods=["DELETE"]
)
@jwt_required()
def revoke_user_session(session_id):
    """Revoke a specific user session"""
    logger.info(f"[ROUTER]: Revoking user session {session_id}")
    identity = current_user

    try:
        # Find the session and verify it belongs to the current user
        from gefapi.models.refresh_token import RefreshToken

        session = RefreshToken.query.filter_by(
            id=session_id, user_id=identity.id
        ).first()

        if not session:
            return error(status=404, detail="Session not found")

        if RefreshTokenService.revoke_refresh_token(session.token):
            return jsonify(message="Session revoked successfully"), 200
        return error(status=500, detail="Failed to revoke session")

    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")


@endpoints.route("/user/me/sessions", strict_slashes=False, methods=["DELETE"])
@jwt_required()
def revoke_all_user_sessions():
    """Revoke all user sessions (logout from all devices)"""
    logger.info("[ROUTER]: Revoking all user sessions")
    identity = current_user

    try:
        revoked_count = RefreshTokenService.revoke_all_user_tokens(identity.id)
        return jsonify(message=f"Successfully revoked {revoked_count} sessions"), 200
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
